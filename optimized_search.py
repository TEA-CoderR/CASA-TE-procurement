"""
优化后的网络搜索模块
实现并发搜索和智能策略
"""
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from ddgs import DDGS


class SmartSearchStrategy:
    """智能搜索策略"""
    
    # EAN类型识别规则
    EAN_PATTERNS = {
        'italian': r'^80\d{8}$',  # 意大利产品
        'german': r'^40\d{8}$',   # 德国产品
        'french': r'^3\d{11}$',   # 法国产品
        'us': r'^0\d{11}$',       # 美国产品
        'uk': r'^50\d{8}$',       # 英国产品
    }
    
    # 针对不同地区的优化查询
    REGION_QUERIES = {
        'italian': [
            'site:ean-search.org "{ean}"',
            'site:amazon.it "{ean}"',
            '"{ean}" prodotto italiano',
        ],
        'german': [
            'site:ean-search.org "{ean}"',
            'site:amazon.de "{ean}"',
            '"{ean}" produkt',
        ],
        'french': [
            'site:ean-search.org "{ean}"',
            'site:amazon.fr "{ean}"',
            '"{ean}" produit',
        ],
        'default': [
            'site:ean-search.org "{ean}"',
            'site:barcodelookup.com "{ean}"',
            '"{ean}" EAN',
        ]
    }
    
    def get_optimized_queries(self, ean, articolo):
        """
        根据EAN类型生成优化查询列表
        
        Returns:
            [query1, query2, ...]
        """
        # 识别EAN地区
        region = 'default'
        for name, pattern in self.EAN_PATTERNS.items():
            if re.match(pattern, ean):
                region = name
                break
        
        # 获取基础查询
        queries = self.REGION_QUERIES.get(region, self.REGION_QUERIES['default'])
        
        # 提取关键词
        keywords = self._extract_keywords(articolo)
        
        # 添加关键词查询（如果有关键词）
        if keywords:
            keyword_str = ' '.join(keywords[:2])
            queries.append(f'"{keyword_str}" "{ean[:6]}"')
        
        # 限制查询数量（最多3个）
        return queries[:3]
    
    def _extract_keywords(self, text):
        """提取关键词"""
        if not text:
            return []
        
        text = str(text).lower()
        stop_words = {'il', 'la', 'le', 'i', 'gli', 'un', 'una', 'di', 'del', 'della', 
                      'con', 'per', 'su', 'in', 'a', 'e', 'o', 'al', 'allo', 'alla'}
        words = [w for w in text.split() if w not in stop_words and len(w) > 2]
        return words


class ConcurrentSearcher:
    """并发网络搜索器"""
    
    def __init__(self, max_workers=5, timeout=10):
        self.max_workers = max_workers
        self.timeout = timeout
        self.strategy = SmartSearchStrategy()
        
    def search_single_ean(self, ean, articolo, log_callback=None):
        """
        搜索单个EAN（优化版）
        
        Returns:
            (found_articolo, source, search_time) 或 (None, None, None)
        """
        search_time = datetime.now().strftime("%H:%M:%S")
        
        if log_callback:
            log_callback(f"[{search_time}] 搜索 EAN: {ean} | 产品: {articolo[:40]}")
        
        try:
            # 获取优化查询
            queries = self.strategy.get_optimized_queries(ean, articolo)
            
            best_result = None
            best_score = 0
            
            with DDGS() as ddgs:
                for i, query in enumerate(queries):
                    try:
                        if log_callback:
                            log_callback(f"  尝试查询 {i+1}/{len(queries)}: {query[:50]}...")
                        
                        results = list(ddgs.text(query, max_results=3))
                        
                        if log_callback:
                            log_callback(f"  找到 {len(results)} 个结果")
                        
                        for r in results:
                            title = r.get('title', '')
                            href = r.get('href', '')
                            body = r.get('body', '')
                            
                            # 过滤掉不相关的结果
                            if 'wikipedia' in href.lower():
                                continue
                        
                            # 计算相关性分数
                            score = 0
                            
                            # EAN完整匹配（最高权重）
                            if ean in (title + body):
                                score += 30
                        
                            # 商品相关词汇
                            product_words = ['prodotto', 'articolo', 'vendita', 'shop', 'store', 'prezzo', '€', 'euro']
                            for word in product_words:
                                if word in (title + body).lower():
                                    score += 3
                        
                            # 电商网站优先
                            ecommerce_sites = ['amazon', 'eBay', 'subito', 'ean-search', 'barcodelookup']
                            for site in ecommerce_sites:
                                if site in href.lower():
                                    score += 15
                        
                            # 如果分数高于当前最佳，更新
                            if score > best_score and score >= 10:  # 最低分数阈值
                                best_score = score
                                best_result = (title[:100], href, search_time)
                                
                                if log_callback:
                                    log_callback(f"  ✔ 最佳结果 (分数:{score}): {title[:60]}...")
                                
                                break  # 找到好的结果就停止当前查询
                        
                    except Exception as e:
                        if log_callback:
                            log_callback(f"  ⚠ 查询失败: {str(e)[:30]}")
                        continue
                        
                    if best_result and best_score >= 20:  # 找到很好的结果就停止
                        break
            
            if best_result:
                if log_callback:
                    log_callback(f"  ✅ 搜索完成: {best_result[0][:50]}...")
                return best_result
            else:
                if log_callback:
                    log_callback(f"  ❌ 无相关结果")
                return None, "无相关结果", search_time
                
        except Exception as e:
            if log_callback:
                log_callback(f"  ❌ 搜索失败: {str(e)[:50]}")
            return None, f"搜索失败: {str(e)[:50]}", search_time
    
    def search_batch(self, items, log_callback=None):
        """
        批量并发搜索
        
        Args:
            items: [(ean, articolo), ...]
            log_callback: 日志回调
            
        Returns:
            {ean: result, ...}
        """
        results = {}
        failed = []
        
        if log_callback:
            log_callback(f"启动并发搜索: {len(items)} 个EAN, {self.max_workers} 个线程")
        
        start_time = time.time()
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # 提交所有任务
            future_to_ean = {
                executor.submit(self.search_single_ean, ean, articolo, log_callback): ean 
                for ean, articolo in items
            }
            
            # 收集结果
            for idx, future in enumerate(as_completed(future_to_ean), 1):
                ean = future_to_ean[future]
                try:
                    result = future.result(timeout=self.timeout)
                    results[ean] = result
                    if log_callback:
                        log_callback(f"[{idx}/{len(items)}] ✔ {ean}")
                except Exception as e:
                    failed.append((ean, str(e)))
                    if log_callback:
                        log_callback(f"[{idx}/{len(items)}] ❌ {ean}: {e}")
        
        elapsed = time.time() - start_time
        if log_callback:
            log_callback(f"搜索完成: {len(results)}成功, {len(failed)}失败, 耗时{elapsed:.1f}秒")
        
        return results, failed


# 全局并发搜索器实例
concurrent_searcher = ConcurrentSearcher(max_workers=5)
