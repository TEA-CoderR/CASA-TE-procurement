"""
自主学习路由模块
根据历史数据自动优化搜索策略和并发参数
"""
import os
import json
import time
from datetime import datetime
from collections import defaultdict


class LearningRouter:
    """
    自主学习路由
    
    特性：
    - 搜索策略学习
    - 并发参数自适应
    - 供应商响应时间预测
    - 动态权重调整
    """
    
    def __init__(self, config_file="learning_router_config.json"):
        """
        Args:
            config_file: 配置文件路径
        """
        self.config_file = config_file
        self.search_stats = defaultdict(lambda: {'success': 0, 'total': 0, 'total_time': 0})
        self.supplier_response = defaultdict(list)  # 供应商响应时间
        self.ean_region_stats = defaultdict(lambda: {'success': 0, 'total': 0})
        self.optimal_workers = 5  # 最优并发数
        self.load_config()
    
    def load_config(self):
        """加载配置"""
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    self.search_stats = defaultdict(
                        lambda: {'success': 0, 'total': 0, 'total_time': 0},
                        config.get('search_stats', {})
                    )
                    self.supplier_response = defaultdict(
                        list,
                        config.get('supplier_response', {})
                    )
                    self.ean_region_stats = defaultdict(
                        lambda: {'success': 0, 'total': 0},
                        config.get('ean_region_stats', {})
                    )
                    self.optimal_workers = config.get('optimal_workers', 5)
            except Exception:
                pass
    
    def save_config(self):
        """保存配置"""
        try:
            config = {
                'timestamp': datetime.now().isoformat(),
                'search_stats': dict(self.search_stats),
                'supplier_response': {k: v[-100:] for k, v in self.supplier_response.items()},
                'ean_region_stats': dict(self.ean_region_stats),
                'optimal_workers': self.optimal_workers
            }
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[LearningRouter] 保存配置失败: {e}")
    
    def record_search_result(self, strategy, ean, success, elapsed_time):
        """
        记录搜索结果
        
        Args:
            strategy: 搜索策略名称
            ean: EAN码
            success: 是否成功
            elapsed_time: 耗时
        """
        stats = self.search_stats[strategy]
        stats['total'] += 1
        stats['total_time'] += elapsed_time
        if success:
            stats['success'] += 1
        
        # 记录EAN地区统计
        region = self._detect_region(ean)
        if region:
            ean_stats = self.ean_region_stats[region]
            ean_stats['total'] += 1
            if success:
                ean_stats['success'] += 1
        
        # 定期保存
        if stats['total'] % 10 == 0:
            self.save_config()
    
    def record_supplier_response(self, supplier, response_time):
        """
        记录供应商响应时间
        
        Args:
            supplier: 供应商名称
            response_time: 响应时间
        """
        self.supplier_response[supplier].append({
            'time': response_time,
            'timestamp': datetime.now().isoformat()
        })
        
        # 只保留最近100条记录
        if len(self.supplier_response[supplier]) > 100:
            self.supplier_response[supplier] = self.supplier_response[supplier][-100:]
    
    def get_best_strategy(self, ean, articolo):
        """
        获取最佳搜索策略
        
        Args:
            ean: EAN码
            articolo: 产品描述
            
        Returns:
            strategy_name: 最佳策略名称
        """
        region = self._detect_region(ean)
        
        # 基于地区选择策略
        if region and region in self.ean_region_stats:
            stats = self.ean_region_stats[region]
            if stats['total'] > 0:
                success_rate = stats['success'] / stats['total']
                # 根据成功率选择策略
                if success_rate > 0.8:
                    return 'fast'  # 快速策略
                elif success_rate > 0.5:
                    return 'balanced'  # 平衡策略
                else:
                    return 'thorough'  # 彻底策略
        
        return 'balanced'  # 默认平衡策略
    
    def get_optimal_workers(self):
        """
        获取最优并发线程数
        
        Returns:
            int: 最优线程数
        """
        # 基于历史数据调整
        total_requests = sum(s['total'] for s in self.search_stats.values())
        
        if total_requests < 10:
            return 3  # 数据少，保守
        elif total_requests < 100:
            return 5  # 中等
        else:
            # 基于成功率调整
            total_success = sum(s['success'] for s in self.search_stats.values())
            success_rate = total_success / total_requests if total_requests > 0 else 0.5
            
            if success_rate > 0.8:
                return 7  # 成功率高，可以更多并发
            elif success_rate > 0.5:
                return 5  # 中等
            else:
                return 3  # 成功率低，减少并发避免超时
        
        return self.optimal_workers
    
    def get_supplier_priority(self, suppliers):
        """
        获取供应商优先级排序
        
        Args:
            suppliers: [supplier_name, ...]
            
        Returns:
            [(supplier_name, priority_score), ...] 按优先级降序
        """
        ranked = []
        for supplier in suppliers:
            responses = self.supplier_response.get(supplier, [])
            if responses:
                # 计算平均响应时间
                avg_time = sum(r['time'] for r in responses[-10:]) / min(10, len(responses))
                # 响应越快优先级越高
                priority = 1.0 / (avg_time + 0.1)
            else:
                priority = 0.5  # 默认优先级
            
            ranked.append((supplier, priority))
        
        ranked.sort(key=lambda x: -x[1])
        return ranked
    
    def _detect_region(self, ean):
        """检测EAN地区"""
        if not ean or len(ean) < 2:
            return None
        
        prefix = ean[:2]
        regions = {
            '80': 'italian',
            '84': 'spanish',
            '40': 'german',
            '30': 'french',
            '35': 'french',
            '00': 'us',
            '01': 'us',
            '50': 'uk',
            '52': 'uk',
        }
        
        return regions.get(prefix)
    
    def get_stats(self):
        """获取路由统计信息"""
        total_requests = sum(s['total'] for s in self.search_stats.values())
        total_success = sum(s['success'] for s in self.search_stats.values())
        
        return {
            'total_requests': total_requests,
            'total_success': total_success,
            'success_rate': total_success / total_requests if total_requests > 0 else 0,
            'optimal_workers': self.get_optimal_workers(),
            'strategy_stats': {
                k: {
                    'success_rate': v['success'] / v['total'] if v['total'] > 0 else 0,
                    'avg_time': v['total_time'] / v['total'] if v['total'] > 0 else 0,
                    'total': v['total']
                }
                for k, v in self.search_stats.items()
            },
            'region_stats': dict(self.ean_region_stats),
            'supplier_count': len(self.supplier_response)
        }
    
    def reset(self):
        """重置学习数据"""
        self.search_stats.clear()
        self.supplier_response.clear()
        self.ean_region_stats.clear()
        self.optimal_workers = 5
        self.save_config()


# 全局学习路由实例
learning_router = LearningRouter()


def record_search(strategy, ean, success, elapsed_time):
    """记录搜索结果（快捷函数）"""
    learning_router.record_search_result(strategy, ean, success, elapsed_time)


def record_supplier(supplier, response_time):
    """记录供应商响应（快捷函数）"""
    learning_router.record_supplier_response(supplier, response_time)


def get_best_strategy(ean, articolo):
    """获取最佳策略（快捷函数）"""
    return learning_router.get_best_strategy(ean, articolo)


def get_optimal_workers():
    """获取最优线程数（快捷函数）"""
    return learning_router.get_optimal_workers()


def get_supplier_priority(suppliers):
    """获取供应商优先级（快捷函数）"""
    return learning_router.get_supplier_priority(suppliers)


def get_router_stats():
    """获取路由统计（快捷函数）"""
    return learning_router.get_stats()
