"""
Excel COM 连接池模块
实现连接复用，避免重复创建/销毁Excel进程
"""
import win32com.client as win32
import threading
import gc
from queue import Queue, Empty
import time


class ExcelConnection:
    """Excel连接包装器"""
    
    def __init__(self, excel_instance):
        self.excel = excel_instance
        self.created_at = time.time()
        self.last_used = time.time()
        self.use_count = 0
    
    def touch(self):
        """更新最后使用时间"""
        self.last_used = time.time()
        self.use_count += 1
    
    def is_expired(self, max_age=300):
        """检查连接是否过期（默认5分钟）"""
        return (time.time() - self.last_used) > max_age


class ExcelPool:
    """
    Excel COM 对象连接池
    
    特性：
    - 连接复用：避免重复创建Excel进程
    - 线程安全：支持多线程并发访问
    - 自动过期：长时间未使用的连接自动回收
    - 连接限制：最大连接数控制
    """
    
    def __init__(self, max_connections=3, max_age=300):
        """
        Args:
            max_connections: 最大连接数
            max_age: 连接最大存活时间（秒），默认5分钟
        """
        self._pool = Queue(maxsize=max_connections)
        self._lock = threading.Lock()
        self._max_connections = max_connections
        self._max_age = max_age
        self._active_count = 0
        self._total_created = 0
        self._total_reused = 0
        
        # 启动清理线程
        self._cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
        self._cleanup_thread.start()
    
    def acquire(self):
        """
        从池获取或创建新的Excel实例
        
        Returns:
            (excel_instance, is_new) 元组
            is_new=True表示新建连接，False表示复用
        """
        with self._lock:
            # 尝试从池获取空闲实例
            try:
                conn = self._pool.get_nowait()
                
                # 检查是否过期
                if conn.is_expired(self._max_age):
                    self._close_connection(conn)
                    return self._create_new_connection()
                
                # 复用连接
                conn.touch()
                self._total_reused += 1
                return conn.excel, False
                
            except Empty:
                # 池为空，创建新实例
                return self._create_new_connection()
    
    def _create_new_connection(self):
        """创建新的Excel连接"""
        if self._active_count >= self._max_connections:
            # 池已满，等待或阻塞获取
            try:
                conn = self._pool.get(timeout=10)
                conn.touch()
                self._total_reused += 1
                return conn.excel, False
            except Empty:
                raise Exception("Excel连接池已满，请稍后重试")
        
        # 创建新实例
        excel = win32.Dispatch("Excel.Application")
        excel.Visible = False
        excel.DisplayAlerts = False
        excel.ScreenUpdating = False
        
        conn = ExcelConnection(excel)
        self._active_count += 1
        self._total_created += 1
        
        return excel, True
    
    def release(self, excel):
        """
        释放Excel实例回池
        
        Args:
            excel: Excel COM对象
        """
        try:
            # 查找对应的连接
            with self._lock:
                # 简单处理：直接放回池
                try:
                    self._pool.put_nowait(excel)
                except:
                    # 池已满，关闭连接
                    self._force_close(excel)
        except Exception as e:
            print(f"[ExcelPool] 释放连接失败: {e}")
    
    def _force_close(self, excel):
        """强制关闭Excel实例"""
        try:
            excel.Quit()
        except:
            pass
        finally:
            del excel
            gc.collect()
            with self._lock:
                self._active_count -= 1
    
    def _close_connection(self, conn):
        """关闭连接"""
        try:
            conn.excel.Quit()
        except:
            pass
        finally:
            del conn.excel
            gc.collect()
            with self._lock:
                self._active_count -= 1
    
    def _cleanup_loop(self):
        """后台清理过期连接"""
        while True:
            time.sleep(60)  # 每分钟检查一次
            self._cleanup_expired()
    
    def _cleanup_expired(self):
        """清理过期连接"""
        # 简单实现：暂不主动清理，依赖获取时检查
        pass
    
    def get_stats(self):
        """获取连接池统计信息"""
        return {
            'max_connections': self._max_connections,
            'active_connections': self._active_count,
            'idle_connections': self._pool.qsize(),
            'total_created': self._total_created,
            'total_reused': self._total_reused,
            'reuse_rate': self._total_reused / max(1, self._total_created + self._total_reused)
        }
    
    def close_all(self):
        """关闭所有连接（程序退出时调用）"""
        while not self._pool.empty():
            try:
                conn = self._pool.get_nowait()
                self._close_connection(conn)
            except:
                pass
        
        with self._lock:
            self._active_count = 0


# 全局连接池实例
excel_pool = ExcelPool(max_connections=5)  # 增加到5个连接


def get_excel():
    """
    获取Excel实例（推荐使用）
    
    Returns:
        excel_instance: Excel COM对象
    """
    return excel_pool.acquire()


def release_excel(excel):
    """释放Excel实例"""
    excel_pool.release(excel)


def get_pool_stats():
    """获取连接池统计"""
    return excel_pool.get_stats()


def close_pool():
    """关闭连接池"""
    excel_pool.close_all()
