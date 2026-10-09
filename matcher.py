"""
缺失查找模块 - 模糊匹配供应商文件中的产品
支持 EAN 精确匹配、ARTICOLO 关键词匹配和网络搜索辅助
优化版本：并发搜索 + 智能查询策略 + AI辅助匹配 + 自主学习路由
"""
from i18n import tr
import os
import re
import difflib
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from openpyxl import load_workbook

from utils import clean_ean, is_valid_xlsx

# 导入AI辅助匹配和自主学习路由
try:
    from ai_matcher import ai_matcher, calculate_ai_similarity, learn_from_match, get_supplier_ranking
    from learning_router import learning_router, record_search, get_best_strategy, get_optimal_workers, get_supplier_priority
    USE_AI = True
except ImportError:
    USE_AI = False


class SmartSearchStrategy:
    """智能搜索策略 - 根据EAN类型优化查询"""
    
    # EAN类型识别规则
    EAN_PATTERNS = {
        'italian': r'^80\d{8}$',  # 意大利产品
        'german': r'^40\d{8}$',   # 德国产品
        'french': r'^3\d{11}$',   # 法国产品
        'us': r'^0\d{11}$',       # 美国产品
        'uk': r'^50\d{8}$',       # 英国产品
    }
    
    # 搜索结果缓存
    _search_cache = {}
    
    # DuckDuckGo 意大利地区设置
    DDGS_REGION = 'it-it'  # 意大利地区
    
    # 针对不同地区的优化查询（使用更有效的搜索语法）
    REGION_QUERIES = {
        'italian': [
            # 优先搜索特定网站（用户验证有效的网站）
            'site:cosedicasashop.it "{ean}"',
            'site:losfiziomarket.it "{ean}"',
            'site:cedimadeo.it "{ean}"',
            'site:cliviaprofumi.eu "{ean}"',
            'site:amazon.it "{ean}"',
            'site:ebay.it "{ean}"',
            # 通用搜索
            '"{ean}" prodotto italiano',
            '"{ean}" detersivo',
        ],
        'german': [
            '"{ean}" produkt',
            '"{ean}" artikel',
            '"{ean}" verkauf',
        ],
        'french': [
            '"{ean}" produit',
            '"{ean}" article',
            '"{ean}" vente',
        ],
        'default': [
            '"{ean}" product',
            '"{ean}" barcode',
            '"{ean}" item',
        ]
    }
    
    @staticmethod
    def get_optimized_queries(ean, articolo):
        """
        根据EAN类型生成优化查询列表
        
        Returns:
            [query1, query2, ...] 最多2-3个查询
        """
        # 检查缓存
        cache_key = f"{ean}_{articolo}"
        if cache_key in SmartSearchStrategy._search_cache:
            return SmartSearchStrategy._search_cache[cache_key]
        
        # 识别EAN地区
        region = 'default'
        for name, pattern in SmartSearchStrategy.EAN_PATTERNS.items():
            if re.match(pattern, ean):
                region = name
                break
        
        # 获取基础查询
        queries = SmartSearchStrategy.REGION_QUERIES.get(region, SmartSearchStrategy.REGION_QUERIES['default'])
        
        # 提取关键词
        keywords = extract_keywords(articolo)
        
        # 添加关键词查询（如果有关键词）
        if keywords:
            keyword_str = ' '.join(keywords[:2])
            queries.insert(0, f'"{keyword_str}" "{ean}"')  # 关键词 + 完整EAN
        
        # 限制查询数量（最多3个）
        result = queries[:3]
        
        # 缓存结果
        SmartSearchStrategy._search_cache[cache_key] = result
        
        return result
    
    @staticmethod
    def validate_result(title, body, ean, articolo):
        """
        验证搜索结果是否有效
        
        Returns:
            bool: 结果是否有效
        """
        # 必须包含EAN码
        if ean not in title and ean not in body:
            return False
        
        # 必须包含产品相关词汇（意大利语清洁产品词汇）
        product_words = ['prodotto', 'articolo', 'vendita', 'shop', 'store', 
                        'prezzo', '€', 'euro', 'product', 'item', 'barcode',
                        'detersivo', 'pulizia', 'igiene', 'candeggina', 'sapon',
                        'profumo', 'cosmetico', 'farmacia', 'parafarmacia']
        text = (title + body).lower()
        
        has_product_word = any(word in text for word in product_words)
        
        # 如果有关键词，检查是否匹配
        if articolo:
            keywords = extract_keywords(articolo)
            if keywords:
                has_keyword = any(kw.lower() in text for kw in keywords[:3])
                return has_product_word and has_keyword
        
        return has_product_word


def search_web_ean(ean, articolo, log_callback=None):
    """
    使用 DuckDuckGo 意大利地区搜索 EAN 码
    优化策略：搜索EAN -> 过滤验证网站 -> 优先产品页 -> 提取名称
    返回: (found_articolo, source, search_time) 或 (None, None, None)
    
    Args:
        ean: EAN 条码
        articolo: 产品名称
        log_callback: 日志回调函数，用于输出搜索进度
    """
    search_time = datetime.now().strftime("%H:%M:%S")
    
    # 检查缓存（验证缓存有效性）
    cache_key = f"{ean}_{articolo}"
    if cache_key in SmartSearchStrategy._search_cache.get('results', {}):
        cached = SmartSearchStrategy._search_cache['results'][cache_key]
        # 如果缓存的标题无效（如网站名），清除缓存重新搜索
        if not is_valid_product_title(cached[0]):
            if log_callback:
                log_callback(tr("  [警告] 清除无效缓存: {0}", cached[0]))
            del SmartSearchStrategy._search_cache['results'][cache_key]
        else:
            if log_callback:
                log_callback(tr("[{0}] [缓存] EAN: {1} | {2}", search_time, ean, cached[0][:50]))
            return cached
    
    # 输出搜索开始日志
    if log_callback:
        log_callback(tr("[{0}] 搜索 EAN: {1} | 产品: {2}", search_time, ean, articolo[:40]))
    
    try:
        from ddgs import DDGS
        
        # 用户验证有效的网站列表（按优先级排序）
        # 优先选择具体产品网站，最后才是 eBay/Amazon
        verified_domains = [
            'cosedicasashop.it', 'losfiziomarket.it', 'cedimadeo.it',
            'cliviaprofumi.eu', 'amazon.it', 'ebay.it'
        ]
        
        # 具体产品网站（优先）
        product_sites = ['cosedicasashop.it', 'losfiziomarket.it', 'cedimadeo.it', 'cliviaprofumi.eu']
        
        best_result = None
        product_page_result = None  # 保存具体产品页面结果
        fallback_result = None  # 保存其他网站的高质量结果
        
        with DDGS() as ddgs:
            if log_callback:
                log_callback(tr("  搜索 DuckDuckGo (意大利地区): \"{0}\"", ean))
            
            # 增加重试机制防限流
            results = []
            for attempt in range(3):
                try:
                    results = list(ddgs.text(f'"{ean}"', max_results=10, region='it-it', safesearch='Off'))
                    if results:
                        break
                    time.sleep(1.5)  # 限流等待
                except Exception as e:
                    if log_callback:
                        log_callback(tr("  [重试] 第 {0} 次尝试失败: {1}", attempt+1, str(e)[:30]))
                    time.sleep(2)
            
            if not results:
                if log_callback:
                    log_callback(tr("  [失败] 搜索超时"))
                return None, "搜索超时", search_time

            if log_callback:
                log_callback(tr("  找到 {0} 个结果", len(results)))
            
            # 意大利语关键词白名单（用于过滤非意大利语结果）
            italian_keywords = ['prodotto', 'articolo', 'vendita', 'shop', 'prezzo', '€', 'euro', 
                                'detersivo', 'pulizia', 'igiene', 'spedizione', 'consegna', 'italiano',
                                'ml', 'kg', 'pz', 'pack', 'confezione']
            
            # 2. 过滤验证网站，优先选择产品页面
            for r in results:
                href = r.get('href', '')
                title = r.get('title', '')
                body = r.get('body', '')
                
                # 跳过分类页面
                if '/b/' in href or '/s?' in href:
                    continue
                
                # 清洗标题
                clean_title = clean_product_title(title)
                
                # 验证标题是否有效
                if not is_valid_product_title(clean_title):
                    continue
                
                # 语言过滤：标题或描述中必须包含至少 1 个意大利语词汇
                text_lower = (title + body).lower()
                if not any(kw in text_lower for kw in italian_keywords):
                    continue
                
                # 检查是否在验证网站列表中
                is_verified = any(domain in href for domain in verified_domains)
                is_product_site = any(domain in href for domain in product_sites)
                
                if log_callback:
                    source = tr("验证网站") if is_verified else tr("其他网站")
                    log_callback(tr("  [发现] {0}: {1}...", source, clean_title[:60]))
                
                # 优先保存验证网站的产品页面
                if is_product_site and product_page_result is None:
                    product_page_result = (clean_title, href, search_time)
                elif is_verified and best_result is None:
                    # 验证网站的其他页面（如 eBay 产品页）
                    best_result = (clean_title, href, search_time)
                elif not is_verified and fallback_result is None:
                    # 其他网站的高质量结果（作为备选）
                    # 确保 EAN 在标题或描述中
                    if ean in (title + body):
                        fallback_result = (clean_title, href, search_time)
        
        # 优先返回产品页面结果，验证标题有效性
        # 优先级：验证网站产品页 > 验证网站其他页 > 其他网站高质量结果
        if product_page_result and is_valid_product_title(product_page_result[0]):
            final_result = product_page_result
        elif best_result and is_valid_product_title(best_result[0]):
            final_result = best_result
        elif fallback_result and is_valid_product_title(fallback_result[0]):
            final_result = fallback_result
        else:
            final_result = None
        
        if final_result:
            # 缓存结果
            if 'results' not in SmartSearchStrategy._search_cache:
                SmartSearchStrategy._search_cache['results'] = {}
            SmartSearchStrategy._search_cache['results'][cache_key] = final_result
            
            if log_callback:
                log_callback(tr("  [成功] 提取名称: {0}...", final_result[0][:50]))
            return final_result
        else:
            # 备选方案：直接访问网站搜索
            if log_callback:
                log_callback(tr("  [备选] 直接搜索意大利网站..."))
            
            site_result = search_italian_sites(ean, articolo, log_callback)
            if site_result[0]:
                if 'results' not in SmartSearchStrategy._search_cache:
                    SmartSearchStrategy._search_cache['results'] = {}
                SmartSearchStrategy._search_cache['results'][cache_key] = site_result
                return site_result
            
            if log_callback:
                log_callback(tr("  [失败] 无相关结果"))
            return None, "无相关结果", search_time
            
    except Exception as e:
        if log_callback:
            log_callback(tr("  [失败] 搜索失败: {0}", str(e)[:50]))
        raise e  # 抛出异常以便批次函数统计失败次数


def clean_product_title(title):
    """清洗产品标题，去除网站名、分类信息、末尾标点"""
    if not title:
        return ""
    
    # 去除常见后缀
    for sep in [' | ', ' - ', ' |']:
        if sep in title:
            title = title.split(sep)[0]
    
    # 去除常见网站名/后缀
    website_names = ['shop', 'store', 'online', 'negozio', 'sito ufficiale', 
                    'acquisti', 'eBay', 'amazon', 'market', 'home', 'homepage']
    title_lower = title.lower()
    for name in website_names:
        if name in title_lower:
            idx = title_lower.find(name)
            title = title[:idx].strip()
    
    # 去除末尾标点、空格、残留符号
    title = title.rstrip('.:;,\t\n\r')
    title = ' '.join(title.split())
    
    return title.strip()


def is_valid_product_title(title):
    """
    验证标题是否是有效的产品名称（不是网站通用标题）
    """
    if not title or len(title) < 5:
        return False
    
    # 网站通用标题黑名单
    bad_titles = [
        'cose di casa', 'los fizi', 'cedimadeo', 'clivia profumi',
        'shop', 'store', 'online', 'negozio', 'benvenuto', 'home',
        'homepage', 'home page', 'sito ufficiale', 'acquista online'
    ]
    
    title_lower = title.lower()
    for bad in bad_titles:
        if bad in title_lower:
            return False
    
    return True


def search_italian_sites(ean, articolo, log_callback=None):
    """
    直接在意大利清洁产品网站上搜索EAN
    返回: (found_articolo, source, search_time) 或 (None, None, None)
    """
    search_time = datetime.now().strftime("%H:%M:%S")
    
    # 直接搜索的网站列表（用户验证有效的网站）
    sites = [
        ('cosedicasashop.it', f'https://www.cosedicasashop.it/?s={ean}'),
        ('losfiziomarket.it', f'https://www.losfiziomarket.it/?s={ean}'),
        ('cedimadeo.it', f'https://www.cedimadeo.it/?s={ean}'),
        ('cliviaprofumi.eu', f'https://www.cliviaprofumi.eu/?s={ean}'),
        ('amazon.it', f'https://www.amazon.it/s?k={ean}'),
        ('ebay.it', f'https://www.ebay.it/sch/i.html?_nkw={ean}'),
    ]
    
    for site_name, url in sites:
        try:
            if log_callback:
                log_callback(tr("  搜索网站: {0}", site_name))
            
            # 使用requests库直接获取网页内容
            import requests
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            response = requests.get(url, headers=headers, timeout=10)
            response.encoding = 'utf-8'
            
            # 简单解析HTML，查找EAN
            if ean in response.text:
                # 提取标题或产品描述
                import re
                title_match = re.search(r'<title>(.*?)</title>', response.text, re.IGNORECASE)
                if title_match:
                    title = title_match.group(1)
                    clean_title = clean_product_title(title)
                    
                    # 验证标题是否有效
                    if is_valid_product_title(clean_title):
                        if log_callback:
                            log_callback(tr("  [找到] {0}: {1}", site_name, clean_title[:60]))
                        return clean_title[:100], url, search_time
                    else:
                        if log_callback:
                            log_callback(tr("  [跳过] 标题无效: {0}", clean_title[:40]))
            
        except Exception as e:
            if log_callback:
                log_callback(tr("  [失败] {0}: {1}", site_name, str(e)[:30]))
            continue
    
    return None, "无相关结果", search_time


def search_web_ean_batch(items, max_workers=None, timeout=15, log_callback=None):
    """
    批量并发搜索多个EAN
    优化版本：使用自主学习路由动态调整线程数
    
    Args:
        items: [(ean, articolo), ...]
        max_workers: 并发线程数（None=自动）
        timeout: 单个搜索超时时间（秒）
        log_callback: 日志回调函数
        
    Returns:
        {ean: (found_articolo, source, search_time), ...}
    """
    # 使用自主学习路由获取最优线程数
    if max_workers is None and USE_AI:
        max_workers = get_optimal_workers()
    elif max_workers is None:
        max_workers = 5  # 默认值
    
    if log_callback:
        log_callback(tr("\n[并发搜索] 启动: {0} 个EAN, {1} 个线程", len(items), max_workers))
    
    start_time = time.time()
    
    # 初始化结果字典和失败列表
    results = {}
    failed = []
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # 提交所有任务
        future_to_ean = {
            executor.submit(search_web_ean, ean, articolo, log_callback): ean 
            for ean, articolo in items
        }
        
        # 收集结果
        for idx, future in enumerate(as_completed(future_to_ean), 1):
            ean = future_to_ean[future]
            try:
                result = future.result(timeout=timeout)
                results[ean] = result
                if log_callback:
                    log_callback(tr("[{0}/{1}] [完成] {2}", idx, len(items), ean))
            except Exception as e:
                failed.append((ean, str(e)))
                if log_callback:
                    log_callback(tr("[{0}/{1}] [失败] {2}: {3}", idx, len(items), ean, e))
    
    elapsed = time.time() - start_time
    if log_callback:
        log_callback(tr("[并发搜索] 完成: {0}成功, {1}失败, 耗时{2:.1f}秒", len(results), len(failed), elapsed))
    
    return results, failed


def calculate_three_way_similarity(orig_articolo, web_articolo, supplier_articolo):
    """
    计算三 ARTICOLO 综合相似度
    - orig_articolo: 原始缺失报告中的 ARTICOLO
    - web_articolo: 网络搜索得到的 ARTICOLO
    - supplier_articolo: 供应商文件中的 ARTICOLO
    
    返回: 综合相似度 (0-1)
    """
    similarities = []
    
    # 1. 原始 vs 供应商
    if orig_articolo and supplier_articolo:
        similarities.append(calculate_similarity(orig_articolo, supplier_articolo))
    
    # 2. 网络 vs 供应商（如果有网络结果）
    if web_articolo and supplier_articolo:
        similarities.append(calculate_similarity(web_articolo, supplier_articolo))
    
    # 3. 原始 vs 网络（验证网络搜索结果是否相关）
    if orig_articolo and web_articolo:
        similarities.append(calculate_similarity(orig_articolo, web_articolo))
    
    if not similarities:
        return 0
    
    # 返回最高相似度（任一匹配成功即可）
    return max(similarities)


def read_missing_report(report_path):
    """
    读取缺失报告，返回 [(ean, articolo, count), ...]
    """
    items = []
    try:
        wb = load_workbook(report_path, data_only=True)
        ws = wb.active
        
        # 跳过标题行，找到数据行
        start_row = None
        for i in range(1, ws.max_row + 1):
            val = ws.cell(i, 1).value
            if val and str(val).strip() == "序号":
                start_row = i + 1
                break
        
        if not start_row:
            return items
        
        for i in range(start_row, ws.max_row + 1):
            ean = ws.cell(i, 2).value
            articolo = ws.cell(i, 3).value
            count = ws.cell(i, 4).value
            if ean and articolo:
                items.append((str(ean).strip(), str(articolo).strip(), count))
        
        wb.close()
        return items
    except Exception as e:
        print(f"读取缺失报告失败: {e}")
        return items


def normalize_text(text):
    """
    标准化文本：去除特殊字符、转小写、清理空格
    """
    if not text:
        return ""
    # 转小写
    text = str(text).lower()
    # 去除特殊字符，保留字母、数字、空格
    text = re.sub(r'[^a-z0-9\s]', ' ', text)
    # 清理多余空格
    text = ' '.join(text.split())
    return text


def extract_keywords(text):
    """
    从产品描述中提取关键词
    """
    text = normalize_text(text)
    # 去除常见停用词
    stop_words = {'il', 'la', 'le', 'i', 'gli', 'un', 'una', 'di', 'del', 'della', 
                  'con', 'per', 'su', 'in', 'a', 'e', 'o', 'al', 'allo', 'alla'}
    words = [w for w in text.split() if w not in stop_words and len(w) > 2]
    return words


def calculate_similarity(text1, text2):
    """
    计算两个文本的相似度 (0-1)
    使用多种方法综合评分
    """
    t1 = normalize_text(text1)
    t2 = normalize_text(text2)
    
    if not t1 or not t2:
        return 0
    
    # 方法1: SequenceMatcher 整体相似度
    seq_sim = difflib.SequenceMatcher(None, t1, t2).ratio()
    
    # 方法2: 关键词匹配
    keywords1 = set(extract_keywords(t1))
    keywords2 = set(extract_keywords(t2))
    
    if keywords1 and keywords2:
        keyword_overlap = len(keywords1 & keywords2) / max(len(keywords1), len(keywords2))
    else:
        keyword_overlap = 0
    
    # 方法3: 单词包含率
    words1 = set(t1.split())
    words2 = set(t2.split())
    
    if words1 and words2:
        word_overlap = len(words1 & words2) / max(len(words1), len(words2))
    else:
        word_overlap = 0
    
    # 综合评分（加权平均）
    score = 0.4 * seq_sim + 0.35 * keyword_overlap + 0.25 * word_overlap
    return score


# 全局缓存：供应商文件索引
_supplier_cache = {}

# 导入持久化缓存
try:
    from supplier_cache import get_cached_supplier_index, save_supplier_index, is_supplier_cache_valid
    USE_PERSISTENT_CACHE = True
except ImportError:
    USE_PERSISTENT_CACHE = False


def build_supplier_index(distribuire_path):
    """
    构建供应商文件索引（一次性读取所有供应商数据）
    优化版本：使用持久化缓存
    
    Returns:
        {clean_ean: [(ean_raw, articolo, supplier_file), ...]}
    """
    # 1. 尝试从内存缓存获取
    if distribuire_path in _supplier_cache:
        return _supplier_cache[distribuire_path]
    
    # 2. 尝试从持久化缓存获取
    if USE_PERSISTENT_CACHE:
        cached_index = get_cached_supplier_index(distribuire_path)
        if cached_index:
            _supplier_cache[distribuire_path] = cached_index
            return cached_index
    
    # 3. 构建索引
    index = {}
    
    for file in sorted(os.listdir(distribuire_path)):
        if not file.lower().endswith((".xls", ".xlsx")):
            continue
        
        # 跳过缺失报告
        if "缺失报告" in file or "missing" in file.lower():
            continue
        
        path = os.path.join(distribuire_path, file)
        
        # 检查是否是有效的 xlsx
        if not file.lower().endswith('.xlsx') or not is_valid_xlsx(path):
            continue
        
        try:
            wb = load_workbook(path, data_only=True)
            ws = wb.active
            
            # 查找 EAN 和 ARTICOLO 列
            col_ean = None
            col_articolo = None
            header_row = None
            
            for row_idx in range(1, min(11, ws.max_row + 1)):
                for cell in ws[row_idx]:
                    if cell.value:
                        val = str(cell.value).strip().upper()
                        if 'CODICE EAN' in val or val == 'EAN':
                            col_ean = cell.column
                            header_row = cell.row
                        if 'ARTICOLO' in val or 'DESCRIZIONE' in val:
                            col_articolo = cell.column
                            if not header_row:
                                header_row = cell.row
            
            if not col_ean:
                wb.close()
                continue
            
            # 遍历数据行，构建索引
            start_row = (header_row or 1) + 1
            for i in range(start_row, ws.max_row + 1):
                ean_val = ws.cell(i, col_ean).value
                articolo_val = ws.cell(i, col_articolo).value if col_articolo else ""
                
                if not ean_val:
                    continue
                
                clean = clean_ean(ean_val)
                if clean:
                    if clean not in index:
                        index[clean] = []
                    index[clean].append((str(ean_val), str(articolo_val) if articolo_val else "", file))
            
            wb.close()
        except Exception:
            continue
    
    # 4. 保存到缓存
    _supplier_cache[distribuire_path] = index
    
    # 5. 保存到持久化缓存
    if USE_PERSISTENT_CACHE:
        save_supplier_index(distribuire_path, index)
    
    return index


def search_supplier_files(distribuire_path, target_ean, target_articolo, threshold=0.6, use_web=False, log_callback=None):
    """
    在供应商文件中搜索匹配的产品
    策略：EAN 精确 → EAN 前6位 + 三 ARTICOLO 相似度排序
    优化版本：集成AI辅助匹配和自主学习路由
    
    Returns:
        [(total_sim, ean, articolo, supplier, art_sim, web_art), ...]
        total_sim: 综合相似度
        art_sim: ARTICOLO 单独相似度
        web_art: 网络搜索得到的 ARTICOLO（如果有）
    """
    results = []
    target_clean_ean = clean_ean(target_ean)
    
    # 获取最佳搜索策略（自主学习）
    strategy = 'balanced'
    if USE_AI:
        strategy = get_best_strategy(target_ean, target_articolo)
        if log_callback:
            log_callback(tr("  [AI] 使用策略: {0}", strategy))
    
    # 网络搜索（如果需要）
    web_articolo = None
    web_source = None
    search_time = None
    if use_web and target_ean:
        start_time = time.time()
        web_articolo, web_source, search_time = search_web_ean(target_ean, target_articolo, log_callback)
        elapsed = time.time() - start_time
        
        # 记录搜索结果（自主学习）
        if USE_AI:
            success = web_articolo is not None
            record_search(strategy, target_ean, success, elapsed)
    
    # 构建/获取索引
    index = build_supplier_index(distribuire_path)
    
    # 1. EAN 精确匹配（最高优先级 100%）
    if target_clean_ean and target_clean_ean in index:
        for ean_raw, articolo, supplier in index[target_clean_ean]:
            # AI辅助相似度计算
            if USE_AI:
                ai_score, ai_details = calculate_ai_similarity(
                    target_ean, target_articolo,
                    ean_raw, articolo, supplier
                )
                # 结合AI评分和传统评分
                if web_articolo:
                    art_sim = calculate_three_way_similarity(target_articolo, web_articolo, articolo)
                else:
                    art_sim = calculate_similarity(articolo, target_articolo) if articolo and target_articolo else 0
                
                # AI增强评分：传统评分 * 0.7 + AI评分 * 0.3
                total_sim = art_sim * 0.7 + ai_score * 0.3
            else:
                # 三 ARTICOLO 相似度
                if web_articolo:
                    art_sim = calculate_three_way_similarity(target_articolo, web_articolo, articolo)
                else:
                    art_sim = calculate_similarity(articolo, target_articolo) if articolo and target_articolo else 0
                total_sim = 1.0  # EAN精确匹配
            
            results.append((total_sim, ean_raw, articolo, supplier, art_sim, web_articolo))
            
            # 记录成功匹配（学习）
            if USE_AI and total_sim > threshold:
                learn_from_match(target_ean, target_articolo, ean_raw, articolo, supplier, True)
        
        return sorted(results, key=lambda x: -x[0])[:10]
    
    # 2. EAN 前6位匹配 + 三 ARTICOLO 相似度排序
    if target_clean_ean and len(target_clean_ean) >= 6:
        ean_prefix6 = target_clean_ean[:6]
        prefix_matches = []
        
        for clean, products in index.items():
            if clean.startswith(ean_prefix6):
                for ean_raw, articolo, supplier in products:
                    # AI辅助相似度计算
                    if USE_AI:
                        ai_score, ai_details = calculate_ai_similarity(
                            target_ean, target_articolo,
                            ean_raw, articolo, supplier
                        )
                        if web_articolo:
                            art_sim = calculate_three_way_similarity(target_articolo, web_articolo, articolo)
                        else:
                            art_sim = calculate_similarity(articolo, target_articolo) if articolo and target_articolo else 0
                        
                        # AI增强评分
                        total_sim = 0.4 + 0.3 * art_sim + 0.3 * ai_score
                    else:
                        # 三 ARTICOLO 相似度
                        if web_articolo:
                            art_sim = calculate_three_way_similarity(target_articolo, web_articolo, articolo)
                        else:
                            art_sim = calculate_similarity(articolo, target_articolo) if articolo and target_articolo else 0
                        
                        # 综合评分：EAN前6位匹配(0.4) + 三ARTICOLO相似度(0.6)
                        total_sim = 0.4 + 0.6 * art_sim
                    
                    prefix_matches.append((total_sim, ean_raw, articolo, supplier, art_sim, web_articolo))
        
        if prefix_matches:
            prefix_matches.sort(key=lambda x: -x[0])
            # 记录最佳匹配（学习）
            if USE_AI and prefix_matches:
                best = prefix_matches[0]
                if best[0] > threshold:
                    learn_from_match(target_ean, target_articolo, best[1], best[2], best[3], True)
            return prefix_matches[:10]
    
    # 3. ARTICOLO 关键词匹配（兜底）
    if target_articolo:
        target_keywords = extract_keywords(target_articolo)
        if len(target_keywords) >= 2:
            best_results = []
            for clean, products in index.items():
                for ean_raw, articolo, supplier in products:
                    if articolo:
                        # AI辅助相似度计算
                        if USE_AI:
                            ai_score, ai_details = calculate_ai_similarity(
                                target_ean, target_articolo,
                                ean_raw, articolo, supplier
                            )
                            if web_articolo:
                                art_sim = calculate_three_way_similarity(target_articolo, web_articolo, articolo)
                            else:
                                art_sim = calculate_similarity(articolo, target_articolo)
                            
                            # AI增强评分
                            total_sim = art_sim * 0.7 + ai_score * 0.3
                        else:
                            # 三 ARTICOLO 相似度
                            if web_articolo:
                                art_sim = calculate_three_way_similarity(target_articolo, web_articolo, articolo)
                            else:
                                art_sim = calculate_similarity(articolo, target_articolo)
                            total_sim = art_sim
                        
                        if total_sim >= threshold:
                            best_results.append((total_sim, ean_raw, articolo, supplier, art_sim, web_articolo))
            
            best_results.sort(key=lambda x: -x[0])
            # 记录最佳匹配（学习）
            if USE_AI and best_results:
                best = best_results[0]
                if best[0] > threshold:
                    learn_from_match(target_ean, target_articolo, best[1], best[2], best[3], True)
            return best_results[:10]
    
    # 记录未找到（学习）
    if USE_AI:
        learn_from_match(target_ean, target_articolo, "", "", "", False)
    
    return results


def find_missing_items(distribuire_path, threshold=0.6, use_web=True, log_callback=None):
    """
    查找缺失报告的匹配结果（自动网络搜索）
    优化版本：使用并发搜索提升性能
    
    Args:
        distribuire_path: distribuire 文件夹路径
        threshold: 相似度阈值
        use_web: 是否使用网络搜索辅助（默认 True）
        log_callback: 日志回调函数，用于输出搜索进度
    
    Returns:
        [(ean, articolo, count, matches, web_source), ...] 
        matches 是 [(total_sim, ean, articolo, supplier, art_sim, web_art), ...]
    """
    report_path = os.path.join(distribuire_path, "缺失报告.xlsx")
    
    if not os.path.exists(report_path):
        return [], "未找到缺失报告"
    
    missing_items = read_missing_report(report_path)
    if not missing_items:
        return [], "缺失报告为空"
    
    results = []
    total = len(missing_items)
    
    # 输出搜索开始日志
    if log_callback:
        log_callback(f"\n{'='*60}")
        log_callback(tr("[开始] 缺失查找 - 共 {0} 个缺失项", total))
        log_callback(f"{'='*60}\n")
    
    # 并发网络搜索（如果启用）
    web_results = {}
    if use_web:
        # 过滤出有EAN的项目
        items_to_search = [(ean, articolo) for ean, articolo, count in missing_items if ean]
        
        if items_to_search:
            if log_callback:
                log_callback(tr("[并发搜索] 开始搜索 {0} 个EAN...", len(items_to_search)))
            
            # 使用并发搜索
            web_results, failed = search_web_ean_batch(
                items_to_search, 
                max_workers=5,  # 5个并发线程
                timeout=15,
                log_callback=log_callback
            )
            
            if log_callback and failed:
                log_callback(tr("[警告] {0} 个EAN搜索失败", len(failed)))
    
    # 处理每个缺失项
    for idx, (ean, articolo, count) in enumerate(missing_items):
        # 输出进度
        if log_callback:
            log_callback(tr("[{0}/{1}] 处理 EAN: {2}", idx+1, total, ean))
        
        # 获取网络搜索结果（从并发结果中获取）
        web_articolo, web_source, search_time = None, None, None
        if use_web and ean and ean in web_results:
            web_articolo, web_source, search_time = web_results[ean]
        
        # 搜索供应商文件（使用三 ARTICOLO 匹配）
        matches = search_supplier_files(distribuire_path, ean, articolo, threshold, use_web, log_callback)
        
        results.append((ean, articolo, count, matches, web_source, web_articolo, search_time))
    
    # 输出搜索完成日志
    if log_callback:
        log_callback(f"\n{'='*60}")
        log_callback(tr("[完成] 缺失查找 - 共 {0} 个缺失项", total))
        log_callback(f"{'='*60}\n")
    
    return results, None
