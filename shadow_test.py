"""
影子测试框架
并行运行新旧算法，对比结果和性能，不影响生产流程
"""
import os
import time
import json
import threading
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, Future


class ShadowTestResult:
    """影子测试结果"""
    
    def __init__(self, task_name, old_result=None, new_result=None, 
                 old_time=0, new_time=0, old_error=None, new_error=None):
        self.task_name = task_name
        self.timestamp = datetime.now().isoformat()
        self.old_result = old_result
        self.new_result = new_result
        self.old_time = old_time
        self.new_time = new_time
        self.old_error = old_error
        self.new_error = new_error
        self.speedup = old_time / new_time if new_time > 0 else 0
        self.results_match = self._check_match()
    
    def _check_match(self):
        """检查结果是否一致"""
        if self.old_error or self.new_error:
            return None
        if self.old_result is None and self.new_result is None:
            return True
        if self.old_result is None or self.new_result is None:
            return False
        # 简单比较（复杂类型需要自定义比较逻辑）
        return self.old_result == self.new_result
    
    def to_dict(self):
        """转换为字典"""
        return {
            'task_name': self.task_name,
            'timestamp': self.timestamp,
            'old_time': round(self.old_time, 4),
            'new_time': round(self.new_time, 4),
            'speedup': round(self.speedup, 2),
            'results_match': self.results_match,
            'old_error': self.old_error,
            'new_error': self.new_error
        }


class ShadowTestFramework:
    """
    影子测试框架
    
    特性：
    - 并行运行新旧算法
    - 自动记录性能数据
    - 结果一致性检查
    - 不影响生产流程
    - 支持A/B测试
    """
    
    def __init__(self, results_dir="shadow_results", enabled=True):
        """
        Args:
            results_dir: 结果存储目录
            enabled: 是否启用影子测试
        """
        self.enabled = enabled
        self.results_dir = results_dir
        os.makedirs(results_dir, exist_ok=True)
        self._lock = threading.Lock()
        self._test_count = 0
        self._error_count = 0
    
    def run(self, task_name, old_func, new_func, *args, **kwargs):
        """
        运行影子测试
        
        Args:
            task_name: 任务名称
            old_func: 旧函数
            new_func: 新函数
            *args, **kwargs: 函数参数
            
        Returns:
            旧函数的返回值（生产使用）
        """
        if not self.enabled:
            return old_func(*args, **kwargs)
        
        # 并行执行新旧函数
        with ThreadPoolExecutor(max_workers=2) as executor:
            # 提交任务
            old_future: Future = executor.submit(old_func, *args, **kwargs)
            new_future: Future = executor.submit(new_func, *args, **kwargs)
            
            # 执行旧函数
            old_start = time.time()
            old_result = None
            old_error = None
            try:
                old_result = old_future.result(timeout=60)
            except Exception as e:
                old_error = str(e)
            old_time = time.time() - old_start
            
            # 执行新函数
            new_start = time.time()
            new_result = None
            new_error = None
            try:
                new_result = new_future.result(timeout=60)
            except Exception as e:
                new_error = str(e)
            new_time = time.time() - new_start
        
        # 创建结果对象
        result = ShadowTestResult(
            task_name=task_name,
            old_result=old_result,
            new_result=new_result,
            old_time=old_time,
            new_time=new_time,
            old_error=old_error,
            new_error=new_error
        )
        
        # 保存结果
        self._save_result(result)
        
        # 更新统计
        with self._lock:
            self._test_count += 1
            if old_error or new_error:
                self._error_count += 1
        
        # 返回旧函数结果（生产安全）
        if old_error:
            raise Exception(f"影子测试旧函数失败: {old_error}")
        return old_result
    
    def _save_result(self, result):
        """保存测试结果"""
        try:
            # 按任务名称分文件
            filename = f"{result.task_name}_{datetime.now().strftime('%Y%m%d')}.json"
            filepath = os.path.join(self.results_dir, filename)
            
            # 读取现有数据
            records = []
            if os.path.exists(filepath):
                with open(filepath, 'r', encoding='utf-8') as f:
                    records = json.load(f)
            
            # 追加新记录
            records.append(result.to_dict())
            
            # 保存
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(records, f, ensure_ascii=False, indent=2)
                
        except Exception as e:
            print(f"[ShadowTest] 保存结果失败: {e}")
    
    def get_report(self, task_name=None, days=7):
        """
        生成测试报告
        
        Args:
            task_name: 任务名称（None=所有任务）
            days: 天数
            
        Returns:
            报告字典
        """
        from datetime import timedelta
        
        cutoff = datetime.now() - timedelta(days=days)
        reports = {}
        
        # 扫描结果文件
        for filename in os.listdir(self.results_dir):
            if not filename.endswith('.json'):
                continue
            
            # 提取任务名称
            parts = filename.replace('.json', '').rsplit('_', 1)
            if len(parts) < 2:
                continue
            
            file_task_name = '_'.join(parts[:-1])
            file_date = parts[-1]
            
            # 过滤
            if task_name and file_task_name != task_name:
                continue
            
            try:
                file_date_obj = datetime.strptime(file_date, '%Y%m%d')
                if file_date_obj < cutoff:
                    continue
            except:
                continue
            
            # 读取数据
            filepath = os.path.join(self.results_dir, filename)
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    records = json.load(f)
                
                if file_task_name not in reports:
                    reports[file_task_name] = []
                reports[file_task_name].extend(records)
                
            except:
                continue
        
        # 生成统计
        summary = {}
        for task_name, records in reports.items():
            if not records:
                continue
            
            old_times = [r['old_time'] for r in records if r['old_time'] > 0]
            new_times = [r['new_time'] for r in records if r['new_time'] > 0]
            speedups = [r['speedup'] for r in records if r['speedup'] > 0]
            matches = [r['results_match'] for r in records if r['results_match'] is not None]
            
            summary[task_name] = {
                'test_count': len(records),
                'avg_old_time': sum(old_times) / len(old_times) if old_times else 0,
                'avg_new_time': sum(new_times) / len(new_times) if new_times else 0,
                'avg_speedup': sum(speedups) / len(speedups) if speedups else 0,
                'match_rate': sum(1 for m in matches if m) / len(matches) if matches else 0,
                'error_count': sum(1 for r in records if r['old_error'] or r['new_error'])
            }
        
        return {
            'period_days': days,
            'total_tests': self._test_count,
            'total_errors': self._error_count,
            'task_summaries': summary
        }
    
    def print_report(self, task_name=None, days=7):
        """打印测试报告"""
        report = self.get_report(task_name, days)
        
        print("\n" + "=" * 60)
        print(f"[影子测试报告] 过去 {report['period_days']} 天")
        print("=" * 60)
        print(f"总测试数: {report['total_tests']}")
        print(f"总错误数: {report['total_errors']}")
        
        for task_name, summary in report['task_summaries'].items():
            print(f"\n[任务] {task_name}")
            print(f"  测试次数: {summary['test_count']}")
            print(f"  旧函数平均: {summary['avg_old_time']:.4f}秒")
            print(f"  新函数平均: {summary['avg_new_time']:.4f}秒")
            print(f"  平均加速: {summary['avg_speedup']:.2f}x")
            print(f"  结果一致率: {summary['match_rate']:.2%}")
            print(f"  错误数: {summary['error_count']}")
        
        print("\n" + "=" * 60)


# 全局影子测试实例
shadow_test = ShadowTestFramework(enabled=True)


def run_shadow_test(task_name, old_func, new_func, *args, **kwargs):
    """运行影子测试（快捷函数）"""
    return shadow_test.run(task_name, old_func, new_func, *args, **kwargs)


def get_shadow_report(task_name=None, days=7):
    """获取影子测试报告"""
    return shadow_test.get_report(task_name, days)


def print_shadow_report(task_name=None, days=7):
    """打印影子测试报告"""
    shadow_test.print_report(task_name, days)
