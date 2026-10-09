"""
性能监控系统
实时采集、分析和报告系统性能指标
"""
import os
import time
import json
import threading
try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False
from datetime import datetime
from collections import defaultdict


class PerformanceMonitor:
    """
    性能监控系统
    
    特性：
    - 实时指标采集
    - 自动性能分析
    - 异常检测
    - 可视化报告
    """
    
    def __init__(self, log_dir="performance_logs", enabled=True):
        """
        Args:
            log_dir: 日志存储目录
            enabled: 是否启用监控
        """
        self.enabled = enabled
        self.log_dir = log_dir
        os.makedirs(log_dir, exist_ok=True)
        self._lock = threading.Lock()
        self._metrics = defaultdict(list)
        self._timers = {}
        self._process = psutil.Process() if HAS_PSUTIL else None
    
    def start_timer(self, name):
        """开始计时"""
        if not self.enabled:
            return None
        self._timers[name] = time.time()
        return self._timers[name]
    
    def stop_timer(self, name):
        """停止计时并记录"""
        if not self.enabled or name not in self._timers:
            return 0
        elapsed = time.time() - self._timers[name]
        del self._timers[name]
        self._record_metric(name, elapsed)
        return elapsed
    
    def _record_metric(self, name, value):
        """记录指标"""
        with self._lock:
            self._metrics[name].append({
                'timestamp': datetime.now().isoformat(),
                'value': value
            })
            # 限制存储数量
            if len(self._metrics[name]) > 1000:
                self._metrics[name] = self._metrics[name][-500:]
    
    def record_metric(self, name, value):
        """手动记录指标"""
        self._record_metric(name, value)
    
    def get_metric_stats(self, name):
        """获取指标统计"""
        if name not in self._metrics:
            return None
        
        values = [m['value'] for m in self._metrics[name]]
        if not values:
            return None
        
        return {
            'count': len(values),
            'min': min(values),
            'max': max(values),
            'avg': sum(values) / len(values),
            'sum': sum(values),
            'latest': values[-1]
        }
    
    def get_system_metrics(self):
        """获取系统资源指标"""
        if not HAS_PSUTIL:
            return {
                'cpu_percent': None,
                'memory_percent': None,
                'note': 'psutil not installed'
            }
        
        try:
            return {
                'cpu_percent': psutil.cpu_percent(interval=0.1),
                'memory_percent': psutil.virtual_memory().percent,
                'memory_used_mb': psutil.virtual_memory().used / (1024 * 1024),
                'memory_total_mb': psutil.virtual_memory().total / (1024 * 1024),
                'process_memory_mb': self._process.memory_info().rss / (1024 * 1024),
                'disk_usage_percent': psutil.disk_usage('.').percent
            }
        except Exception as e:
            return {'error': str(e)}
    
    def start_session(self, session_name):
        """开始监控会话"""
        session_data = {
            'name': session_name,
            'start_time': datetime.now().isoformat(),
            'metrics': {},
            'system_start': self.get_system_metrics()
        }
        self._session = session_data
        return session_data
    
    def end_session(self):
        """结束监控会话"""
        if not hasattr(self, '_session'):
            return None
        
        self._session['end_time'] = datetime.now().isoformat()
        self._session['system_end'] = self.get_system_metrics()
        self._session['metrics'] = dict(self._metrics)
        
        # 保存会话
        self._save_session(self._session)
        
        session = self._session
        del self._session
        return session
    
    def _save_session(self, session):
        """保存会话到文件"""
        try:
            filename = f"{session['name']}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            filepath = os.path.join(self.log_dir, filename)
            
            # 转换metrics为可序列化格式
            save_data = {
                'name': session['name'],
                'start_time': session['start_time'],
                'end_time': session.get('end_time'),
                'system_start': session.get('system_start'),
                'system_end': session.get('system_end'),
                'metrics_summary': {}
            }
            
            for name, records in session.get('metrics', {}).items():
                values = [r['value'] for r in records]
                if values:
                    save_data['metrics_summary'][name] = {
                        'count': len(values),
                        'min': min(values),
                        'max': max(values),
                        'avg': sum(values) / len(values)
                    }
            
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(save_data, f, ensure_ascii=False, indent=2)
                
        except Exception as e:
            print(f"[PerformanceMonitor] 保存会话失败: {e}")
    
    def generate_report(self):
        """生成性能报告"""
        report = {
            'timestamp': datetime.now().isoformat(),
            'system': self.get_system_metrics(),
            'metrics': {}
        }
        
        for name in self._metrics:
            stats = self.get_metric_stats(name)
            if stats:
                report['metrics'][name] = stats
        
        return report
    
    def print_report(self):
        """打印性能报告"""
        report = self.generate_report()
        
        print("\n" + "=" * 60)
        print("[性能监控报告]")
        print("=" * 60)
        
        print("\n[系统资源]")
        system = report.get('system', {})
        print(f"  CPU使用率: {system.get('cpu_percent', 'N/A')}%")
        print(f"  内存使用率: {system.get('memory_percent', 'N/A')}%")
        print(f"  进程内存: {system.get('process_memory_mb', 0):.1f}MB")
        
        print("\n[性能指标]")
        for name, stats in report.get('metrics', {}).items():
            print(f"\n  {name}:")
            print(f"    次数: {stats['count']}")
            print(f"    最小: {stats['min']:.4f}秒")
            print(f"    最大: {stats['max']:.4f}秒")
            print(f"    平均: {stats['avg']:.4f}秒")
            print(f"    总计: {stats['sum']:.4f}秒")
        
        print("\n" + "=" * 60)
    
    def export_to_file(self, filepath=None):
        """导出性能数据到文件"""
        if filepath is None:
            filepath = os.path.join(self.log_dir, 
                                    f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        
        report = self.generate_report()
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        
        return filepath
    
    def reset(self):
        """重置所有数据"""
        with self._lock:
            self._metrics.clear()
            self._timers.clear()


# 全局性能监控实例
perf_monitor = PerformanceMonitor(enabled=True)


def monitor_timer(name):
    """装饰器：自动计时"""
    def decorator(func):
        def wrapper(*args, **kwargs):
            perf_monitor.start_timer(name)
            try:
                result = func(*args, **kwargs)
                return result
            finally:
                perf_monitor.stop_timer(name)
        return wrapper
    return decorator


def start_session(name):
    """开始监控会话"""
    return perf_monitor.start_session(name)


def end_session():
    """结束监控会话"""
    return perf_monitor.end_session()


def get_report():
    """获取性能报告"""
    return perf_monitor.generate_report()


def print_report():
    """打印性能报告"""
    perf_monitor.print_report()


def export_report(filepath=None):
    """导出性能报告"""
    return perf_monitor.export_to_file(filepath)
