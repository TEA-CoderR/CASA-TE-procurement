"""
性能基准测试脚本
用于测量采购系统各组件的执行时间
"""
import time
import os
import sys
from datetime import datetime

# 设置输出编码
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils import calculate_formulas, convert_to_xlsx
from matcher import search_web_ean, build_supplier_index


def benchmark_excel_operations(test_dir, log=True):
    """测试Excel操作性能"""
    if log:
        print("\n" + "="*60)
        print("[测试1] Excel操作性能测试")
        print("="*60)
    
    results = {}
    
    # 测试1: Excel COM 公式计算
    excel_files = []
    for root, dirs, files in os.walk(test_dir):
        for file in files:
            if file.lower().endswith((".xls", ".xlsx")) and "缺失" not in file:
                excel_files.append(os.path.join(root, file))
    
    if not excel_files:
        if log:
            print("[警告] 未找到Excel文件")
        return results
    
    # 限制测试文件数量
    excel_files = excel_files[:5]
    
    times = []
    for file_path in excel_files:
        if log:
            print(f"\n测试文件: {os.path.basename(file_path)}")
        
        start = time.time()
        try:
            calc_path = calculate_formulas(file_path)
            elapsed = time.time() - start
            times.append(elapsed)
            if log:
                print(f"  [成功] 公式计算: {elapsed:.2f}秒")
        except Exception as e:
            if log:
                print(f"  [失败] 错误: {e}")
    
    if times:
        results['excel_formula_calc'] = {
            'avg': sum(times) / len(times),
            'min': min(times),
            'max': max(times),
            'count': len(times)
        }
        if log:
            print(f"\n平均时间: {results['excel_formula_calc']['avg']:.2f}秒")
    
    return results


def benchmark_web_search(test_eans, use_concurrent=False, log=True):
    """测试网络搜索性能"""
    if log:
        print("\n" + "="*60)
        print("[测试2] 网络搜索性能测试")
        print("="*60)
    
    results = {}
    
    # 串行搜索
    if log:
        print(f"\n测试串行搜索 ({len(test_eans)} 个EAN)...")
    
    start = time.time()
    serial_results = []
    for ean, articolo in test_eans:
        try:
            result = search_web_ean(ean, articolo, log_callback=None if not log else lambda x: None)
            serial_results.append(result)
        except Exception as e:
            serial_results.append(None)
    
    serial_time = time.time() - start
    results['serial'] = {
        'total_time': serial_time,
        'avg_per_ean': serial_time / len(test_eans) if test_eans else 0,
        'count': len(test_eans)
    }
    
    if log:
        print(f"  总时间: {serial_time:.2f}秒")
        print(f"  平均/EAN: {results['serial']['avg_per_ean']:.2f}秒")
    
    # 并发搜索（如果启用）
    if use_concurrent:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        
        if log:
            print(f"\n测试并发搜索 (5线程)...")
        
        start = time.time()
        concurrent_results = []
        
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {
                executor.submit(search_web_ean, ean, articolo, lambda x: None): ean
                for ean, articolo in test_eans
            }
            
            for future in as_completed(futures):
                try:
                    concurrent_results.append(future.result(timeout=10))
                except Exception as e:
                    concurrent_results.append(None)
        
        concurrent_time = time.time() - start
        results['concurrent'] = {
            'total_time': concurrent_time,
            'avg_per_ean': concurrent_time / len(test_eans) if test_eans else 0,
            'count': len(test_eans),
            'speedup': serial_time / concurrent_time if concurrent_time > 0 else 0
        }
        
        if log:
            print(f"  总时间: {concurrent_time:.2f}秒")
            print(f"  平均/EAN: {results['concurrent']['avg_per_ean']:.2f}秒")
            print(f"  加速比: {results['concurrent']['speedup']:.2f}x")
    
    return results


def benchmark_supplier_index(distribuire_path, log=True):
    """测试供应商索引构建性能"""
    if log:
        print("\n" + "="*60)
        print("[测试3] 供应商索引构建性能测试")
        print("="*60)
    
    results = {}
    
    # 第一次构建（无缓存）
    if log:
        print("\n第一次构建（无缓存）...")
    
    start = time.time()
    index1 = build_supplier_index(distribuire_path)
    time1 = time.time() - start
    
    results['first_build'] = {
        'time': time1,
        'items': len(index1)
    }
    
    if log:
        print(f"  时间: {time1:.2f}秒")
        print(f"  索引项数: {len(index1)}")
    
    # 第二次构建（有缓存）
    if log:
        print("\n第二次构建（有缓存）...")
    
    start = time.time()
    index2 = build_supplier_index(distribuire_path)
    time2 = time.time() - start
    
    results['cached_build'] = {
        'time': time2,
        'items': len(index2),
        'speedup': time1 / time2 if time2 > 0 else 0
    }
    
    if log:
        print(f"  时间: {time2:.2f}秒")
        print(f"  加速比: {results['cached_build']['speedup']:.2f}x")
    
    return results


def run_full_benchmark(test_dir):
    """运行完整基准测试"""
    print("\n" + "="*60)
    print("[基准测试] 采购系统性能测试")
    print(f"测试时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"测试目录: {test_dir}")
    print("="*60)
    
    all_results = {}
    
    # 1. Excel操作测试
    all_results['excel'] = benchmark_excel_operations(test_dir)
    
    # 2. 网络搜索测试
    test_eans = [
        ("8012345678901", "SAPONE LIQUIDO MANI 500ML"),
        ("8012345678902", "CANDIDA DETERGENTE PAVIMENTI 1L"),
        ("8012345678903", "AMUCHINA CANDeggIANTE 1L"),
    ]
    all_results['search'] = benchmark_web_search(test_eans, use_concurrent=True)
    
    # 3. 供应商索引测试
    distribuire_path = os.path.join(test_dir, "distribuire")
    if os.path.exists(distribuire_path):
        all_results['supplier_index'] = benchmark_supplier_index(distribuire_path)
    
    # 输出总结
    print("\n" + "="*60)
    print("[总结] 性能测试报告")
    print("="*60)
    
    if 'excel' in all_results and 'excel_formula_calc' in all_results['excel']:
        excel = all_results['excel']['excel_formula_calc']
        print(f"\n[Excel] 公式计算:")
        print(f"  平均时间: {excel['avg']:.2f}秒")
        print(f"  测试次数: {excel['count']}")
    
    if 'search' in all_results:
        search = all_results['search']
        print(f"\n[网络搜索] 性能对比:")
        if 'serial' in search:
            print(f"  串行总时间: {search['serial']['total_time']:.2f}秒")
            print(f"  串行平均/EAN: {search['serial']['avg_per_ean']:.2f}秒")
        if 'concurrent' in search:
            print(f"  并发总时间: {search['concurrent']['total_time']:.2f}秒")
            print(f"  并发平均/EAN: {search['concurrent']['avg_per_ean']:.2f}秒")
            print(f"  加速比: {search['concurrent']['speedup']:.2f}x")
    
    if 'supplier_index' in all_results:
        idx = all_results['supplier_index']
        print(f"\n[供应商索引] 构建性能:")
        if 'first_build' in idx:
            print(f"  首次构建: {idx['first_build']['time']:.2f}秒")
        if 'cached_build' in idx:
            print(f"  缓存构建: {idx['cached_build']['time']:.2f}秒")
            print(f"  加速比: {idx['cached_build']['speedup']:.2f}x")
    
    print("\n" + "="*60)
    
    return all_results


if __name__ == "__main__":
    # 测试目录
    test_dir = r"C:\Users\UFFICIO\Desktop\2604 FOIANO\ordine1"
    
    if not os.path.exists(test_dir):
        print(f"[错误] 测试目录不存在: {test_dir}")
        sys.exit(1)
    
    # 运行基准测试
    results = run_full_benchmark(test_dir)
    
    # 保存结果
    import json
    results_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 
                                 f"benchmark_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    
    with open(results_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    
    print(f"\n[完成] 结果已保存到: {results_file}")
