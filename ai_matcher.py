"""
AI辅助匹配模块
使用机器学习和启发式算法提升供应商匹配准确率
"""
import os
import re
import json
import time
from datetime import datetime
from collections import defaultdict
import difflib


class AIMatcher:
    """
    AI辅助匹配器
    
    特性：
    - 多维度相似度评分
    - 历史学习（从成功匹配中学习）
    - 供应商信誉评分
    - 自适应权重调整
    """
    
    def __init__(self, learning_file="ai_matcher_learning.json"):
        """
        Args:
            learning_file: 学习数据存储文件
        """
        self.learning_file = learning_file
        self.supplier_scores = {}  # 供应商信誉评分
        self.ean_patterns = {}  # EAN模式学习
        self.keyword_weights = {}  # 关键词权重
        self.load_learning_data()
    
    def load_learning_data(self):
        """加载学习数据"""
        if os.path.exists(self.learning_file):
            try:
                with open(self.learning_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.supplier_scores = data.get('supplier_scores', {})
                    self.ean_patterns = data.get('ean_patterns', {})
                    self.keyword_weights = data.get('keyword_weights', {})
            except Exception:
                pass
    
    def save_learning_data(self):
        """保存学习数据"""
        try:
            data = {
                'timestamp': datetime.now().isoformat(),
                'supplier_scores': self.supplier_scores,
                'ean_patterns': self.ean_patterns,
                'keyword_weights': self.keyword_weights
            }
            with open(self.learning_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[AIMatcher] 保存学习数据失败: {e}")
    
    def calculate_ai_similarity(self, target_ean, target_articolo, 
                                 supplier_ean, supplier_articolo, supplier_name):
        """
        计算AI辅助相似度评分
        
        Args:
            target_ean: 目标EAN
            target_articolo: 目标产品描述
            supplier_ean: 供应商EAN
            supplier_articolo: 供应商产品描述
            supplier_name: 供应商名称
            
        Returns:
            (score, details) 元组
        """
        details = {}
        total_score = 0
        
        # 1. EAN相似度 (权重: 40%)
        ean_score = self._calculate_ean_similarity(target_ean, supplier_ean)
        details['ean_score'] = ean_score
        total_score += ean_score * 0.4
        
        # 2. 产品描述相似度 (权重: 30%)
        art_score = self._calculate_articolo_similarity(target_articolo, supplier_articolo)
        details['art_score'] = art_score
        total_score += art_score * 0.3
        
        # 3. 关键词匹配 (权重: 15%)
        keyword_score = self._calculate_keyword_match(target_articolo, supplier_articolo)
        details['keyword_score'] = keyword_score
        total_score += keyword_score * 0.15
        
        # 4. 供应商信誉 (权重: 10%)
        supplier_score = self._get_supplier_score(supplier_name)
        details['supplier_score'] = supplier_score
        total_score += supplier_score * 0.1
        
        # 5. 模式匹配 (权重: 5%)
        pattern_score = self._calculate_pattern_match(target_ean, target_articolo)
        details['pattern_score'] = pattern_score
        total_score += pattern_score * 0.05
        
        return total_score, details
    
    def _calculate_ean_similarity(self, ean1, ean2):
        """计算EAN相似度"""
        if not ean1 or not ean2:
            return 0
        
        # 精确匹配
        if ean1 == ean2:
            return 1.0
        
        # 清理后匹配
        clean1 = re.sub(r'[^0-9]', '', ean1)
        clean2 = re.sub(r'[^0-9]', '', ean2)
        if clean1 == clean2:
            return 0.95
        
        # 前缀匹配（前6位）
        if len(clean1) >= 6 and len(clean2) >= 6:
            if clean1[:6] == clean2[:6]:
                return 0.7
        
        # 序列相似度
        return difflib.SequenceMatcher(None, clean1, clean2).ratio() * 0.5
    
    def _calculate_articolo_similarity(self, art1, art2):
        """计算产品描述相似度"""
        if not art1 or not art2:
            return 0
        
        # 标准化
        norm1 = self._normalize_text(art1)
        norm2 = self._normalize_text(art2)
        
        # 序列相似度
        seq_sim = difflib.SequenceMatcher(None, norm1, norm2).ratio()
        
        # 单词重叠
        words1 = set(norm1.split())
        words2 = set(norm2.split())
        if words1 and words2:
            word_overlap = len(words1 & words2) / max(len(words1), len(words2))
        else:
            word_overlap = 0
        
        # 综合评分
        return 0.6 * seq_sim + 0.4 * word_overlap
    
    def _calculate_keyword_match(self, art1, art2):
        """计算关键词匹配度"""
        if not art1 or not art2:
            return 0
        
        # 提取关键词
        keywords1 = self._extract_keywords(art1)
        keywords2 = self._extract_keywords(art2)
        
        if not keywords1 or not keywords2:
            return 0
        
        # 计算关键词重叠（考虑权重）
        match_score = 0
        for kw in keywords1:
            if kw in keywords2:
                weight = self.keyword_weights.get(kw, 1.0)
                match_score += weight
        
        return min(1.0, match_score / len(keywords1))
    
    def _get_supplier_score(self, supplier_name):
        """获取供应商信誉评分"""
        if not supplier_name:
            return 0.5  # 默认中等评分
        
        score = self.supplier_scores.get(supplier_name, 0.5)
        return score
    
    def _calculate_pattern_match(self, ean, articolo):
        """计算模式匹配度"""
        # 基于EAN前缀识别产品类型
        if not ean or len(ean) < 6:
            return 0.5
        
        prefix = ean[:6]
        pattern_score = self.ean_patterns.get(prefix, 0.5)
        return pattern_score
    
    def _normalize_text(self, text):
        """标准化文本"""
        if not text:
            return ""
        text = str(text).lower()
        text = re.sub(r'[^a-z0-9\s]', ' ', text)
        text = ' '.join(text.split())
        return text
    
    def _extract_keywords(self, text):
        """提取关键词"""
        text = self._normalize_text(text)
        # 意大利语停用词
        stop_words = {'il', 'la', 'le', 'i', 'gli', 'un', 'una', 'di', 'del', 'della', 
                      'con', 'per', 'su', 'in', 'a', 'e', 'o', 'al', 'allo', 'alla',
                      'the', 'and', 'or', 'of', 'to', 'in', 'is', 'it'}
        words = [w for w in text.split() if w not in stop_words and len(w) > 2]
        return words
    
    def learn_from_match(self, target_ean, target_articolo, 
                         matched_ean, matched_articolo, matched_supplier, success):
        """
        从匹配结果中学习
        
        Args:
            target_ean: 目标EAN
            target_articolo: 目标产品描述
            matched_ean: 匹配的EAN
            matched_articolo: 匹配的产品描述
            matched_supplier: 匹配的供应商
            success: 是否成功匹配
        """
        if success:
            # 提升供应商评分
            current_score = self.supplier_scores.get(matched_supplier, 0.5)
            self.supplier_scores[matched_supplier] = min(1.0, current_score + 0.05)
            
            # 学习EAN模式
            if matched_ean and len(matched_ean) >= 6:
                prefix = matched_ean[:6]
                current_pattern = self.ean_patterns.get(prefix, 0.5)
                self.ean_patterns[prefix] = min(1.0, current_pattern + 0.02)
            
            # 学习关键词权重
            keywords = self._extract_keywords(target_articolo)
            for kw in keywords:
                current_weight = self.keyword_weights.get(kw, 1.0)
                self.keyword_weights[kw] = min(2.0, current_weight + 0.1)
            
            # 保存学习数据
            self.save_learning_data()
    
    def get_supplier_ranking(self, suppliers):
        """
        对供应商进行排序
        
        Args:
            suppliers: [supplier_name, ...]
            
        Returns:
            [(supplier_name, score), ...] 按评分降序排列
        """
        ranked = []
        for supplier in suppliers:
            score = self._get_supplier_score(supplier)
            ranked.append((supplier, score))
        
        ranked.sort(key=lambda x: -x[1])
        return ranked
    
    def get_stats(self):
        """获取AI匹配器统计信息"""
        return {
            'supplier_count': len(self.supplier_scores),
            'pattern_count': len(self.ean_patterns),
            'keyword_count': len(self.keyword_weights),
            'top_suppliers': sorted(self.supplier_scores.items(), 
                                   key=lambda x: -x[1])[:5] if self.supplier_scores else [],
            'top_keywords': sorted(self.keyword_weights.items(), 
                                  key=lambda x: -x[1])[:10] if self.keyword_weights else []
        }


# 全局AI匹配器实例
ai_matcher = AIMatcher()


def calculate_ai_similarity(target_ean, target_articolo, 
                           supplier_ean, supplier_articolo, supplier_name):
    """计算AI相似度（快捷函数）"""
    return ai_matcher.calculate_ai_similarity(
        target_ean, target_articolo, 
        supplier_ean, supplier_articolo, supplier_name
    )


def learn_from_match(target_ean, target_articolo, 
                    matched_ean, matched_articolo, matched_supplier, success):
    """从匹配中学习（快捷函数）"""
    ai_matcher.learn_from_match(
        target_ean, target_articolo,
        matched_ean, matched_articolo, matched_supplier, success
    )


def get_supplier_ranking(suppliers):
    """获取供应商排名（快捷函数）"""
    return ai_matcher.get_supplier_ranking(suppliers)


def get_ai_stats():
    """获取AI匹配器统计（快捷函数）"""
    return ai_matcher.get_stats()
