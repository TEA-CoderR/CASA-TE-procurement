"""
供应商索引持久化缓存模块
实现索引缓存到磁盘，避免重复构建
"""
import os
import json
import hashlib
from datetime import datetime


class SupplierIndexCache:
    """
    供应商索引持久化缓存
    
    特性：
    - 文件哈希检测：只在文件变化时重建索引
    - 时间过期：缓存超过1小时自动失效
    - JSON存储：轻量级持久化
    """
    
    def __init__(self, cache_dir=".cache", max_age_hours=1):
        """
        Args:
            cache_dir: 缓存目录
            max_age_hours: 缓存最大存活时间（小时）
        """
        self.cache_dir = cache_dir
        self.max_age_hours = max_age_hours
        os.makedirs(cache_dir, exist_ok=True)
    
    def _get_cache_path(self, distribuire_path):
        """根据路径生成缓存文件"""
        path_hash = hashlib.md5(distribuire_path.encode()).hexdigest()
        return os.path.join(self.cache_dir, f"supplier_index_{path_hash}.json")
    
    def _get_file_hash(self, path):
        """计算文件哈希（用于检测变化）"""
        try:
            # 只读取前8KB计算哈希（快速检测）
            with open(path, 'rb') as f:
                data = f.read(8192)
            return hashlib.md5(data).hexdigest()
        except Exception:
            return None
    
    def is_cache_valid(self, distribuire_path):
        """
        检查缓存是否有效
        
        Returns:
            bool: 缓存是否有效
        """
        cache_path = self._get_cache_path(distribuire_path)
        if not os.path.exists(cache_path):
            return False
        
        try:
            with open(cache_path, 'r', encoding='utf-8') as f:
                cache_data = json.load(f)
            
            # 检查时间过期
            cache_time = datetime.fromisoformat(cache_data['timestamp'])
            age_hours = (datetime.now() - cache_time).total_seconds() / 3600
            
            if age_hours > self.max_age_hours:
                return False
            
            # 检查文件是否变化
            stored_hashes = cache_data.get('file_hashes', {})
            for file_name, stored_hash in stored_hashes.items():
                file_path = os.path.join(distribuire_path, file_name)
                if os.path.exists(file_path):
                    current_hash = self._get_file_hash(file_path)
                    if current_hash != stored_hash:
                        return False
            
            return True
            
        except Exception:
            return False
    
    def get_cached_index(self, distribuire_path):
        """
        获取缓存的索引
        
        Returns:
            dict or None: 索引数据，None表示缓存无效
        """
        if not self.is_cache_valid(distribuire_path):
            return None
        
        cache_path = self._get_cache_path(distribuire_path)
        try:
            with open(cache_path, 'r', encoding='utf-8') as f:
                cache_data = json.load(f)
            return cache_data.get('index', {})
        except Exception:
            return None
    
    def save_index(self, distribuire_path, index):
        """
        保存索引到缓存
        
        Args:
            distribuire_path: distribuire文件夹路径
            index: 索引数据 {clean_ean: [(ean_raw, articolo, supplier), ...]}
        """
        cache_path = self._get_cache_path(distribuire_path)
        
        try:
            # 计算文件哈希
            file_hashes = {}
            for file in os.listdir(distribuire_path):
                if file.lower().endswith((".xls", ".xlsx")):
                    path = os.path.join(distribuire_path, file)
                    file_hash = self._get_file_hash(path)
                    if file_hash:
                        file_hashes[file] = file_hash
            
            cache_data = {
                'timestamp': datetime.now().isoformat(),
                'distribuire_path': distribuire_path,
                'file_hashes': file_hashes,
                'index_size': len(index),
                'index': index  # 注意：大索引可能占用较多磁盘空间
            }
            
            with open(cache_path, 'w', encoding='utf-8') as f:
                json.dump(cache_data, f, ensure_ascii=False)
            
            return True
            
        except Exception as e:
            print(f"[SupplierCache] 保存缓存失败: {e}")
            return False
    
    def clear_cache(self, distribuire_path=None):
        """
        清除缓存
        
        Args:
            distribuire_path: 指定路径，None则清除所有缓存
        """
        if distribuire_path:
            cache_path = self._get_cache_path(distribuire_path)
            if os.path.exists(cache_path):
                os.remove(cache_path)
        else:
            for file in os.listdir(self.cache_dir):
                if file.startswith("supplier_index_") and file.endswith(".json"):
                    path = os.path.join(self.cache_dir, file)
                    if os.path.exists(path):
                        os.remove(path)
    
    def get_stats(self):
        """获取缓存统计信息"""
        cache_files = [f for f in os.listdir(self.cache_dir) 
                       if f.startswith("supplier_index_") and f.endswith(".json")]
        
        total_size = sum(os.path.getsize(os.path.join(self.cache_dir, f)) 
                        for f in cache_files)
        
        return {
            'cache_dir': self.cache_dir,
            'cache_files': len(cache_files),
            'total_size_kb': total_size / 1024,
            'max_age_hours': self.max_age_hours
        }


# 全局缓存实例
supplier_cache = SupplierIndexCache(cache_dir=".cache", max_age_hours=1)


def get_cached_supplier_index(distribuire_path):
    """
    获取缓存的供应商索引
    
    Args:
        distribuire_path: distribuire文件夹路径
        
    Returns:
        dict or None: 索引数据
    """
    return supplier_cache.get_cached_index(distribuire_path)


def save_supplier_index(distribuire_path, index):
    """保存供应商索引到缓存"""
    return supplier_cache.save_index(distribuire_path, index)


def is_supplier_cache_valid(distribuire_path):
    """检查供应商缓存是否有效"""
    return supplier_cache.is_cache_valid(distribuire_path)


def get_cache_stats():
    """获取缓存统计"""
    return supplier_cache.get_stats()


def clear_supplier_cache():
    """清除供应商缓存"""
    supplier_cache.clear_cache()
