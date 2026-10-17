#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
数据模型和决策逻辑模块 - 业务逻辑核心
"""

import math
import pandas as pd
import numpy as np
from typing import Tuple, List
from .config import DecisionParams


class ReorderDecision:
    """订单建议决策引擎"""
    
    def __init__(self, reorder_cycle: int = 30):
        """
        初始化决策引擎
        
        Args:
            reorder_cycle: 订货周期（天），默认 30 天
        """
        self.params = DecisionParams.to_dict()
        self.reorder_cycle = reorder_cycle  # 可配置的订货周期
    
    def calculate_inventory_days(self, store_inventory: int, sales_period: int, 
                                 period_days: int) -> float:
        """计算库存可销售天数"""
        if sales_period <= 0:
            return float('inf')
        daily_sales = sales_period / period_days
        if daily_sales <= 0:
            return float('inf')
        if store_inventory <= 0:
            return 0
        return store_inventory / daily_sales
    
    def classify_sales_tier(self, sales_30: int, avg_sales_30: float) -> str:
        """Classifica livello vendite: A/B/C"""
        if sales_30 >= avg_sales_30 * self.params['tier_a_threshold']:
            return 'A'
        elif sales_30 >= avg_sales_30 * self.params['tier_b_threshold']:
            return 'B'
        else:
            return 'C'
    
    def calculate_trend_ratio(self, sales_30: int, sales_90: int) -> float:
        """Calcola rapporto trend vendite"""
        if sales_90 - sales_30 == 0:
            return 0
        recent_daily = sales_30 / 30.0
        older_daily = (sales_90 - sales_30) / 60.0
        if older_daily == 0:
            return 1.0 if recent_daily > 0 else 0
        return (recent_daily - older_daily) / older_daily
    
    def calculate_volatility(self, sales_30: int, sales_60: int, sales_90: int) -> float:
        """
        Calcola volatilità vendite (coefficiente di variazione = deviazione standard/media)
        
        Usa vendite giornaliere medie di tre periodi per calcolare volatilità
        """
        if sales_30 == 0 and sales_60 == 0 and sales_90 == 0:
            return 0
        
        # Calcola vendite giornaliere medie per ogni periodo
        daily_30 = sales_30 / 30.0
        daily_60 = sales_60 / 60.0
        daily_90 = sales_90 / 90.0
        
        # Calcola media e deviazione standard
        mean_daily = np.mean([daily_30, daily_60, daily_90])
        if mean_daily == 0:
            return 0
        
        std_daily = np.std([daily_30, daily_60, daily_90])
        
        # Coefficiente di variazione (volatilità)
        volatility = std_daily / mean_daily
        return volatility
    
    def calculate_dynamic_safety_factor(self, volatility: float) -> float:
        """
        Calcola fattore di sicurezza dinamico in base alla volatilità
        
        Maggiore volatilità -> fattore di sicurezza più alto -> inventario più abbondante
        """
        min_sf = self.params['min_safety_factor']
        max_sf = self.params['max_safety_factor']
        high_threshold = self.params['volatility_high_threshold']
        
        # Interpolazione lineare: volatilità 0 -> min_sf, volatilità high_threshold -> max_sf
        if volatility >= high_threshold:
            return max_sf
        elif volatility <= 0:
            return min_sf
        else:
            # Interpolazione lineare
            return min_sf + (max_sf - min_sf) * (volatility / high_threshold)
    
    def calculate_trend_adjustment(self, trend_ratio: float) -> float:
        """
        Calcola fattore di aggiustamento quantità ordine in base al trend
        
        Trend in salita -> aumenta quantità ordine
        Trend in discesa -> riduce quantità ordine
        """
        weight = self.params['trend_weight']
        max_adjustment = self.params['max_trend_adjustment']
        
        # Limita ampiezza aggiustamento in [-max_adjustment, max_adjustment]
        adjustment = max(-max_adjustment, min(max_adjustment, trend_ratio * weight))
        
        # Restituisce fattore di aggiustamento (1 + adjustment)
        return 1 + adjustment
    
    def get_decision(self, row, avg_sales_30: float, days_1: int, 
                     days_2: int, days_3: int) -> Tuple[str, float, str, float, int]:
        """
        Ottieni decisione suggerimento ordine
        
        Returns:
            Tuple: (decision_text, inventory_days, sales_tier, trend_ratio, priority)
        """
        sales_1 = row.get('sales_1', 0) or 0
        sales_2 = row.get('sales_2', 0) or 0
        sales_3 = row.get('sales_3', 0) or 0
        
        inventory_days = self.calculate_inventory_days(row['store_inventory'], sales_1, days_1)
        sales_tier = self.classify_sales_tier(sales_1, avg_sales_30)
        trend_ratio = self.calculate_trend_ratio(sales_1, sales_3)
        
        # Decisione livello urgente
        if inventory_days < self.params['emergency_days'] and sales_tier == 'A':
            return ('🔴【Rifornimento urgente】Esaurito in 3 giorni!', inventory_days, 
                   sales_tier, trend_ratio, 10)
        if inventory_days < self.params['emergency_days'] and sales_tier == 'B':
            return ('🔴【Rifornimento urgente】Esaurito in 3 giorni!', inventory_days, 
                   sales_tier, trend_ratio, 9)
        if inventory_days < self.params['critical_days'] and sales_tier in ['A', 'B']:
            return ('🔴【Ordine necessario】Inventario critico', inventory_days, 
                   sales_tier, trend_ratio, 8)
        if inventory_days < self.params['urgent_days'] and sales_tier == 'A' and trend_ratio > self.params['trend_up_threshold']:
            return ('🔴【Ordine necessario】Alte vendite in crescita', inventory_days, 
                   sales_tier, trend_ratio, 7)
        
        # Decisione livello suggerito
        if inventory_days < self.params['normal_days'] and sales_tier in ['A', 'B']:
            return ('🟡【Ordine suggerito】Rifornimento normale', inventory_days, 
                   sales_tier, trend_ratio, 5)
        if inventory_days < self.params['urgent_days'] and trend_ratio > self.params['trend_up_threshold']:
            return ('🟡【Ordine suggerito】Vendite in crescita', inventory_days, 
                   sales_tier, trend_ratio, 4)
        
        # Decisione livello non ordinare
        if row['total_inventory'] > 0 and sales_1 == 0:
            return ('🟢【Non ordinare】Inventario lento', inventory_days, 
                   sales_tier, trend_ratio, 1)
        if inventory_days > self.params['excess_days'] and sales_tier == 'C':
            return ('🟢【Non ordinare】Inventario eccessivo', inventory_days, 
                   sales_tier, trend_ratio, 1)
        if inventory_days > self.params['normal_days'] and trend_ratio < self.params['trend_down_threshold']:
            return ('🟢【Non ordinare】Vendite in calo', inventory_days, 
                   sales_tier, trend_ratio, 2)
        
        # Predefinito: osserva
        return ('🟠【Osserva】Monitoraggio continuo', inventory_days, 
               sales_tier, trend_ratio, 3)
    
    def calculate_suggested_order_qty(self, inventory_days: float, sales_30: int, 
                                      sales_60: int, sales_90: int,  # Nuovo: per calcolare volatilità
                                      store_inventory: int,  # Inventario singolo negozio
                                      trend_ratio: float = 0,  # Nuovo: rapporto trend
                                      lead_time_days: int = None, 
                                      moq: int = 1, 
                                      packaging: int = 1) -> int:
        """
        Calcola quantità ordine suggerita (basata su inventario singolo negozio, sicurezza dinamica e trend)
        
        Formula:
        1. Calcola volatilità -> fattore di sicurezza dinamico
        2. Calcola fattore di aggiustamento trend
        3. Inventario target = vendite giornaliere × (ciclo riordino + lead time) × fattore sicurezza × fattore trend
        4. Quantità ordine = max(0, inventario target - inventario corrente)
        """
        if lead_time_days is None:
            lead_time_days = self.params['lead_time_days']
        
        if inventory_days < self.params['normal_days'] and sales_30 > 0:
            daily_sales = sales_30 / 30
            
            # 1️⃣ Calcola volatilità
            volatility = self.calculate_volatility(sales_30, sales_60, sales_90)
            
            # 2️⃣ Calcola fattore di sicurezza dinamico in base alla volatilità
            dynamic_safety_factor = self.calculate_dynamic_safety_factor(volatility)
            
            # 3️⃣ Calcola fattore di aggiustamento in base al trend
            trend_adjustment_factor = self.calculate_trend_adjustment(trend_ratio)
            
            # 4️⃣ Calcola inventario target (combina fattore sicurezza dinamico e aggiustamento trend)
            # 💡 Obiettivo: portare inventario singolo negozio a "livello per vendere (ciclo riordino + lead time) giorni × fattore sicurezza × aggiustamento trend"
            target_inventory = daily_sales * (self.reorder_cycle + lead_time_days) * \
                             dynamic_safety_factor * trend_adjustment_factor
            
            # 5️⃣ Calcola quantità ordine suggerita
            raw_order_qty = max(0, math.ceil(target_inventory - store_inventory))
            
            # 6️⃣ Considera confezione e quantità minima ordine
            if packaging > 1:
                order_qty = math.ceil(raw_order_qty / packaging) * packaging
            else:
                order_qty = raw_order_qty
            
            return max(moq, order_qty) if order_qty > 0 else 0
        return 0
    
    def calculate_packing_adjusted_qty(self, suggested_order_qty: int, 
                                       packaging: int,  # Quantità confezione
                                       last_in_qty: int,  # LastInQuantita
                                       sales_tier: str,  # Categoria A/B/C
                                       inventory_days: float,  # Giorni inventario
                                       trend_ratio: float) -> Tuple[int, int]:
        """
        Calcola quantità ordine dopo aggiustamento confezione
        
        Logica:
        1. ⭐ Se packaging == 0 (无标准包装): restituisce (suggested_order_qty, 0) direttamente
        2. Seleziona valore prioritario: se packaging > 0, usa packaging; altrimenti usa last_in_qty
        3. Calcola numero confezioni suggerito = suggested_order_qty / valore prioritario
        4. Arrotonda in base alle condizioni:
           - Categoria A o inventario molto basso o trend in salita: arrotonda per eccesso (ceil)
           - Categoria B: arrotonda (round)
           - Categoria C o altro: arrotonda per difetto (floor)
        5. Quantità ordine finale = numero confezioni arrotondato × valore prioritario
        
        Returns:
            Tuple: (adjusted_order_qty, packing_qty)
        """
        if suggested_order_qty <= 0:
            return (0, 0)
        
        # ⭐ 1️⃣ 无标准包装（packaging == 0）：直接使用建议订单量，不计算 Colli
        if packaging == 0:
            return (suggested_order_qty, 0)
        
        # 2️⃣ Seleziona valore prioritario
        if packaging > 0:
            priority_value = packaging
        elif last_in_qty > 0:
            priority_value = last_in_qty
        else:
            # Se entrambi i valori non sono > 0, restituisce quantità suggerita originale
            return (suggested_order_qty, 0)
        
        # 2️⃣ Calcola numero confezioni suggerito
        suggested_packing_qty = suggested_order_qty / priority_value
        
        # 3️⃣ Giudica se necessita arrotondamento per eccesso
        is_tier_a = sales_tier == 'A-Alte vendite'
        is_tier_b = sales_tier == 'B-Vendite medie'
        is_low_inventory = inventory_days < self.params['urgent_days']  # Inventario inferiore a 14 giorni
        is_trend_up = trend_ratio > self.params['trend_up_threshold']  # Trend in salita
        
        # 4️⃣ Arrotonda in base alle condizioni
        if is_tier_a or is_low_inventory or is_trend_up:
            # Arrotonda per eccesso (ceil)
            packing_qty = math.ceil(suggested_packing_qty)
        elif is_tier_b:
            # Arrotonda (round)
            packing_qty = round(suggested_packing_qty)
        else:
            # Arrotonda per difetto (floor)
            packing_qty = math.floor(suggested_packing_qty)
        
        # Assicurati che numero confezioni sia almeno 1 (se necessita ordine)
        packing_qty = max(1, packing_qty)
        
        # 5️⃣ Calcola quantità ordine finale
        adjusted_order_qty = packing_qty * priority_value
        
        return (adjusted_order_qty, packing_qty)


class SeasonalityAnalyzer:
    """
    季节性产品分析器
    
    通过分析产品销售数据的月度变化来判断产品是否为季节性产品。
    对比数据：
    - 去年前3个月的每月销售量
    - 今年前3个月的每月销售量
    - 今年当月的销售量
    
    新增功能：预测销售高峰持续到几月份
    """
    
    def __init__(self, seasonality_threshold: float = 1.5):
        """
        Args:
            seasonality_threshold: 季节性判定阈值，当月销量与前3月平均销量的比值
                                  超过此阈值判定为季节性产品
        """
        self.seasonality_threshold = seasonality_threshold
    
    def predict_peak_end_month(self, monthly_sales: dict, current_month: int, current_year: int) -> dict:
        """
        预测销售高峰持续到几月份
        
        逻辑：
        1. 分析去年同期的销售趋势，找到高峰开始和结束月份
        2. 对比今年趋势，如果今年增长更快/更慢，调整预测
        3. 返回预测的高峰结束月份和预计剩余销售月数
        
        Args:
            monthly_sales: 月度销售数据字典
            current_month: 当前月份
            current_year: 当前年份
            
        Returns:
            dict: {
                'peak_end_month': 预测高峰结束月份 (1-12),
                'peak_end_year': 预测高峰结束年份,
                'remaining_peak_months': 预计剩余高峰月数,
                'last_year_peak_end': 去年高峰结束月份,
                'confidence': 预测置信度 (0-1)
            }
        """
        result = {
            'peak_end_month': None,
            'peak_end_year': current_year,
            'remaining_peak_months': 0,
            'last_year_peak_end': None,
            'confidence': 0.0
        }
        
        if not monthly_sales:
            return result
        
        # 获取去年和今年的月度数据
        last_year = current_year - 1
        last_year_months = {}
        this_year_months = {}
        
        for year_month, sales in monthly_sales.items():
            year, month = map(int, year_month.split('-'))
            if year == last_year:
                last_year_months[month] = sales
            elif year == current_year:
                this_year_months[month] = sales
        
        # 如果没有去年同期数据，无法预测
        if not last_year_months:
            return result
        
    # 1. 分析去年后3个月的销售趋势（关键！）
        # 获取去年当月后3个月的数据
        last_year_next3 = []
        for i in range(1, 4):
            month = current_month + i
            year = last_year
            if month > 12:
                month -= 12
                year += 1
            last_year_next3.append(last_year_months.get(month, 0))
        
        # 计算去年后3个月的趋势
        last_year_current_sales = last_year_months.get(current_month, 0)
        last_year_next3_avg = sum(last_year_next3) / 3 if last_year_next3 else 0
        
        # 判断去年后3个月是上升、平稳还是下降
        if last_year_current_sales > 0:
            next3_trend_ratio = last_year_next3_avg / last_year_current_sales
        else:
            next3_trend_ratio = 1
        
        # 2. 找到去年高峰模式
        last_peak_month = max(last_year_months, key=last_year_months.get) if last_year_months else None
        last_peak_sales = last_year_months.get(last_peak_month, 0)
        
        if last_peak_sales == 0:
            return result
        
        # 计算去年各月相对于高峰月的销售比率
        last_year_ratios = {m: s / last_peak_sales for m, s in last_year_months.items()}
        
        # 找到去年高峰结束月份（销售下降到高峰的 60% 以下）
        last_year_peak_end = None
        months_sorted = sorted(last_year_months.keys())
        
        for month in months_sorted:
            if month > last_peak_month and last_year_ratios.get(month, 0) < 0.6:
                last_year_peak_end = month - 1
                break
        
        if last_year_peak_end is None:
            last_year_peak_end = max(months_sorted)
        
        result['last_year_peak_end'] = last_year_peak_end
        
        # 3. 计算去年高峰持续时间
        peak_start = min(months_sorted) if months_sorted else last_peak_month
        last_year_peak_duration = last_year_peak_end - peak_start + 1
        
        # 4. 对比今年趋势，调整预测
        this_year_current = this_year_months.get(current_month, 0)
        this_year_prev_avg = sum(this_year_months.get(m, 0) for m in range(1, current_month)) / max(current_month - 1, 1)
        
        trend_ratio = this_year_current / (this_year_prev_avg + 0.01) if this_year_prev_avg > 0 else 1
        
        last_year_same_month = last_year_months.get(current_month, 0)
        yoy_ratio = this_year_current / (last_year_same_month + 0.01) if last_year_same_month > 0 else 1
        
        # 5. 预测今年高峰结束月份（核心：基于去年后3个月趋势）
        predicted_peak_end = peak_start + last_year_peak_duration - 1
        
        # 根据去年后3个月趋势调整（关键逻辑）
        if next3_trend_ratio > 1.1:
            # 去年后3个月还在上升，说明旺季很长
            predicted_peak_end = min(12, predicted_peak_end + 3)
            confidence = 0.75
        elif next3_trend_ratio > 0.9:
            # 去年后3个月平稳，旺季持续
            predicted_peak_end = min(12, predicted_peak_end + 1)
            confidence = 0.85
        elif next3_trend_ratio > 0.6:
            # 去年后3个月缓慢下降
            confidence = 0.8
        else:
            # 去年后3个月快速下降，旺季较短
            predicted_peak_end = max(current_month, predicted_peak_end - 1)
            confidence = 0.7
        
        # 根据今年表现进一步调整
        if trend_ratio > 1.3 and yoy_ratio > 1.2:
            predicted_peak_end = min(12, predicted_peak_end + 2)
            confidence = min(1.0, confidence + 0.1)
        elif trend_ratio < 0.8 and yoy_ratio < 0.8:
            predicted_peak_end = max(current_month, predicted_peak_end - 1)
            confidence = min(1.0, confidence + 0.1)
        
        # 限制在合理范围内
        predicted_peak_end = max(current_month, min(12, predicted_peak_end))
        
        # 如果预测超过12月，跨年
        if predicted_peak_end >= 12 and current_month < 6:
            # 如果当前是上半年，高峰可能跨年
            result['peak_end_year'] = current_year + 1 if predicted_peak_end > 12 else current_year
            predicted_peak_end = predicted_peak_end % 12 if predicted_peak_end > 12 else predicted_peak_end
        
        result['peak_end_month'] = predicted_peak_end
        result['remaining_peak_months'] = max(0, predicted_peak_end - current_month)
        result['confidence'] = round(confidence, 2)
        
        return result
    
    def analyze(self, monthly_sales: dict, current_month: int, current_year: int) -> dict:
        """
        分析单个产品的季节性
        
        Args:
            monthly_sales: 月度销售数据字典，格式为 {'YYYY-MM': sales_qty, ...}
            current_month: 当前月份 (1-12)
            current_year: 当前年份
            
        Returns:
            dict 包含季节性分析结果
        """
        result = {
            'is_seasonal': False,
            'seasonality_score': 0.0,
            # 去年数据
            'last_year_month1': 0,
            'last_year_month2': 0,
            'last_year_month3': 0,
            'last_year_avg_prev3': 0.0,
            'last_year_current_month': 0,  # 去年当月
            'last_year_month5': 0,
            'last_year_month6': 0,
            'last_year_month7': 0,
            'last_year_avg_next3': 0.0,  # 去年后3月平均
            # 今年数据
            'this_year_month1': 0,
            'this_year_month2': 0,
            'this_year_month3': 0,
            'this_year_avg_prev3': 0.0,
            'current_month_sales': 0,
            # 对比指标
            'yoy_prev3_change': 0.0,  # 前3月同比
            'yoy_current_month_change': 0.0,  # 当月同比
            'peak_end_month': None,
            'remaining_peak_months': 0,
            'prediction_confidence': 0.0,
            'description': ''
        }
        
        if not monthly_sales:
            result['description'] = '无销售数据'
            return result
        
        # ⭐ 新增：检查是否有至少一个月销量 ≥ 5件（季节性产品门槛）
        # 如果没有月份达到5件以上，直接判定为非季节性产品
        has_significant_sales = any(v >= 5 for v in monthly_sales.values())
        if not has_significant_sales:
            result['description'] = '🟢 非季节性产品 - 销量过低（无月份≥5件）'
            return result
        
        last_year = current_year - 1
        
        # 去年前3个月
        for i in range(1, 4):
            key = f"{last_year}-{i:02d}"
            result[f'last_year_month{i}'] = monthly_sales.get(key, 0)
        result['last_year_avg_prev3'] = sum([result['last_year_month1'], result['last_year_month2'], 
                                              result['last_year_month3']]) / 3
        
        # 去年当月
        last_year_current_key = f"{last_year}-{current_month:02d}"
        result['last_year_current_month'] = monthly_sales.get(last_year_current_key, 0)
        
        # 去年后3个月
        next3_values = []
        for i in range(1, 4):
            month = current_month + i
            year = last_year
            if month > 12:
                month -= 12
                year += 1
            key = f"{year}-{month:02d}"
            result[f'last_year_month{current_month+i}'] = monthly_sales.get(key, 0)
            next3_values.append(monthly_sales.get(key, 0))
        result['last_year_avg_next3'] = sum(next3_values) / 3
        
        # 今年前3个月
        for i in range(1, 4):
            key = f"{current_year}-{i:02d}"
            result[f'this_year_month{i}'] = monthly_sales.get(key, 0)
        result['this_year_avg_prev3'] = sum([result['this_year_month1'], result['this_year_month2'], 
                                              result['this_year_month3']]) / 3
        
        # 今年当月
        current_key = f"{current_year}-{current_month:02d}"
        result['current_month_sales'] = monthly_sales.get(current_key, 0)
        
        # 计算同比变化率
        if result['last_year_avg_prev3'] > 0:
            result['yoy_prev3_change'] = round((result['this_year_avg_prev3'] - result['last_year_avg_prev3']) / result['last_year_avg_prev3'] * 100, 2)
        
        if result['last_year_current_month'] > 0:
            result['yoy_current_month_change'] = round((result['current_month_sales'] - result['last_year_current_month']) / result['last_year_current_month'] * 100, 2)
        
        # 计算季节性得分
        all_values = list(monthly_sales.values())
        if not all_values or max(all_values) == 0:
            result['description'] = '销售数据不足'
            return result
        
        # 计算变异系数
        mean_sales = sum(all_values) / len(all_values)
        if mean_sales == 0:
            result['description'] = '平均销量为零'
            return result
        
        variance = sum((x - mean_sales) ** 2 for x in all_values) / len(all_values)
        std_dev = variance ** 0.5
        cv = std_dev / mean_sales
        
        # 季节性得分
        seasonality_score = min(1.0, cv / 1.0)
        result['seasonality_score'] = round(seasonality_score, 2)
        
        # 判断季节性
        current_vs_avg = (result['current_month_sales'] / (result['this_year_avg_prev3'] + 0.01)) if result['this_year_avg_prev3'] > 0 else 1
        
        is_peak_month = current_vs_avg >= self.seasonality_threshold
        
        # 预测高峰结束时间
        peak_prediction = self.predict_peak_end_month(monthly_sales, current_month, current_year)
        result['peak_end_month'] = peak_prediction['peak_end_month']
        result['remaining_peak_months'] = peak_prediction['remaining_peak_months']
        result['prediction_confidence'] = peak_prediction['confidence']
        
       # 生成单月销售建议
        monthly_recommendations = self.generate_monthly_recommendations(
            result, monthly_sales, current_month, current_year, is_peak_month, seasonality_score
        )
        result['monthly_recommendations'] = monthly_recommendations
        
        # 综合判断
        if seasonality_score >= 0.4 or is_peak_month:
            result['is_seasonal'] = True
            
            if result['yoy_current_month_change'] > 20 and is_peak_month:
                if result['remaining_peak_months'] > 0:
                    result['description'] = f'🔴 季节性产品 - 销售旺季，同比去年当月+{result["yoy_current_month_change"]:.0f}%，预计持续到 {result["peak_end_month"]}月（还有{result["remaining_peak_months"]}个月）'
                else:
                    result['description'] = f'🔴 季节性产品 - 销售旺季，同比去年当月+{result["yoy_current_month_change"]:.0f}%'
            elif result['yoy_prev3_change'] > 20:
                result['description'] = f'🟡 季节性产品 - 前3月同比去年+{result["yoy_prev3_change"]:.0f}%'
            elif is_peak_month:
                if result['remaining_peak_months'] > 0:
                    result['description'] = f'🟡 季节性产品 - 当月为销售高峰，预计持续到 {result["peak_end_month"]}月'
                else:
                    result['description'] = '🟡 季节性产品 - 当月为销售高峰'
            elif result['yoy_current_month_change'] < -20:
                result['description'] = f'🟠 可能季节性产品 - 同比去年当月-{abs(result["yoy_current_month_change"]):.0f}%'
            else:
                result['description'] = '🟠 可能季节性产品 - 销售波动较大'
        else:
            if abs(result['yoy_current_month_change']) < 10:
                result['description'] = '🟢 非季节性产品 - 销售平稳，同比变化小'
            else:
                result['description'] = '🟢 非季节性产品 - 销售平稳'
        
        return result
    
    def generate_monthly_recommendations(self, result: dict, monthly_sales: dict, 
                                        current_month: int, current_year: int,
                                        is_peak_month: bool, seasonality_score: float) -> dict:
        """
        生成单月销售建议
        
        根据每月的销售数据和季节性分析，给出每月的备货建议
        
        Returns:
            dict: {月份: 建议}
        """
        recommendations = {}
        
        # 获取去年和今年的月度数据
        last_year = current_year - 1
        last_year_months = {}
        this_year_months = {}
        
        for year_month, sales in monthly_sales.items():
            year, month = map(int, year_month.split('-'))
            if year == last_year:
                last_year_months[month] = sales
            elif year == current_year:
                this_year_months[month] = sales
        
        # 为今年已过去的月份生成建议
        for month in range(1, current_month + 1):
            this_month_sales = this_year_months.get(month, 0)
            last_month_sales = last_year_months.get(month, 0)
            
            # 计算同比
            if last_month_sales > 0:
                yoy = (this_month_sales - last_month_sales) / last_month_sales * 100
            else:
                yoy = 0
            
            # 生成建议
            if this_month_sales >= result.get('current_month_sales', 0) * 0.8 and is_peak_month:
                recommendations[f'{month}月'] = '🔴 旺季月份 - 保持高库存'
            elif yoy > 20:
                recommendations[f'{month}月'] = '🟡 增长月份 - 适度增加备货'
            elif yoy < -20:
                recommendations[f'{month}月'] = '🟠 下滑月份 - 减少备货'
            elif this_month_sales >= 5:
                recommendations[f'{month}月'] = '🟢 正常月份 - 常规备货'
            else:
                recommendations[f'{month}月'] = '⚪ 低销量月份 - 谨慎备货'
        
        # 为未来月份生成预测建议（基于去年后3个月趋势）
        if result.get('remaining_peak_months', 0) > 0:
            for i in range(1, result['remaining_peak_months'] + 1):
                future_month = current_month + i
                if future_month <= 12:
                    last_year_future = last_year_months.get(future_month, 0)
                    if last_year_future >= 5:
                        recommendations[f'{future_month}月(预测)'] = '🔴 预计旺季 - 提前备货'
                    else:
                        recommendations[f'{future_month}月(预测)'] = '🟡 预计平稳 - 正常备货'
        
        return recommendations
    
    def analyze_batch(self, sales_df: pd.DataFrame, current_month: int, current_year: int) -> pd.DataFrame:
        """
        批量分析产品季节性
        """
        results = []
        
        for _, row in sales_df.iterrows():
            cod_articolo = row['CodArticolo']
            
            # 构建月度销售字典
            monthly_sales = {}
            for col in row.index:
                if col.startswith('sales_') and col != 'sales_':
                    year_month = col.replace('sales_', '')
                    monthly_sales[year_month] = int(row[col]) if pd.notna(row[col]) else 0
            
            # 分析
            result = self.analyze(monthly_sales, current_month, current_year)
            result['CodArticolo'] = cod_articolo
            results.append(result)
        
        return pd.DataFrame(results)


class ProductDataProcessor:
    """Processore dati prodotti"""
    
    @staticmethod
    def process_reorder_data(inventory_df: pd.DataFrame,
                            suppliers_df: pd.DataFrame,
                            sales_dfs: dict,
                            margin_dfs: dict,
                            avg_sales_30: float,
                            days_list: List[int],
                            reorder_cycle: int = 30,
                            supplier_reorder_cycles: dict = None) -> pd.DataFrame:
        """
        Elabora dati suggerimenti ordini
        
        Args:
            inventory_df: DataFrame dati inventario
            suppliers_df: DataFrame mappatura fornitori
            sales_dfs: Dizionario dati vendite {df_sales_1, df_sales_2, df_sales_3}
            margin_dfs: Dizionario dati margine {df_margin_1, df_margin_2, df_margin_3}
            avg_sales_30: Vendite medie 30 giorni
            days_list: Lista giorni [days_1, days_2, days_3]
            reorder_cycle: Ciclo di riordino (giorni), predefinito 30 giorni (valore globale)
            supplier_reorder_cycles: Dizionario {supplier_id: reorder_cycle} per fornitore specifico,
                                   se None usa reorder_cycle globale per tutti
        
        Returns:
            DataFrame dati completo elaborato
        """
        # ⭐ 使用全局默认 reorder_cycle 创建引擎（供应商特定值在下面逐行覆盖）
        decision_engine = ReorderDecision(reorder_cycle=reorder_cycle)
        
        # Crea mappatura fornitori
        product_supplier_map = suppliers_df.groupby('CodArticolo')['IDFornitore'].first().to_dict()
        # ⭐ 修复：确保供应商 ID 是 int 类型（数据库可能返回 float）
        product_supplier_map = {k: int(v) for k, v in product_supplier_map.items()}
        
        inventory_df = inventory_df.copy()
        inventory_df['IDFornitore'] = inventory_df['CodArticolo'].map(product_supplier_map)
        
        # Unisci dati vendite
        result_df = inventory_df.merge(sales_dfs['df_sales_1'], on='CodArticolo', how='left')
        result_df = result_df.rename(columns={'sales_qty': 'sales_1'})
        result_df = result_df.merge(sales_dfs['df_sales_2'], on='CodArticolo', how='left')
        result_df = result_df.rename(columns={'sales_qty': 'sales_2'})
        result_df = result_df.merge(sales_dfs['df_sales_3'], on='CodArticolo', how='left')
        result_df = result_df.rename(columns={'sales_qty': 'sales_3'})
        result_df = result_df.merge(sales_dfs['df_sales_4'], on='CodArticolo', how='left')
        result_df = result_df.rename(columns={'sales_qty': 'sales_4'})
        # ⭐ 新增：sales_5 = period_days 销量
        if 'df_sales_5' in sales_dfs:
            result_df = result_df.merge(sales_dfs['df_sales_5'], on='CodArticolo', how='left')
            result_df = result_df.rename(columns={'sales_qty': 'sales_5'})
        
        # Unisci dati margine
        if 'df_margin_1' in margin_dfs:
            result_df = result_df.merge(margin_dfs['df_margin_1'][['CodArticolo', 'total_guadagno', 'total_net_sales']], 
                                       on='CodArticolo', how='left')
            result_df = result_df.rename(columns={'total_guadagno': 'guadagno_1', 'total_net_sales': 'net_sales_1'})
        if 'df_margin_2' in margin_dfs:
            result_df = result_df.merge(margin_dfs['df_margin_2'][['CodArticolo', 'total_guadagno', 'total_net_sales']], 
                                       on='CodArticolo', how='left')
            result_df = result_df.rename(columns={'total_guadagno': 'guadagno_2', 'total_net_sales': 'net_sales_2'})
        if 'df_margin_3' in margin_dfs:
            result_df = result_df.merge(margin_dfs['df_margin_3'][['CodArticolo', 'total_guadagno', 'total_net_sales']], 
                                       on='CodArticolo', how='left')
            result_df = result_df.rename(columns={'total_guadagno': 'guadagno_3', 'total_net_sales': 'net_sales_3'})
        
        # Conversione tipi di dato
        result_df['store_inventory'] = result_df['store_inventory'].fillna(0).astype(int)
        result_df['total_inventory'] = result_df['total_inventory'].fillna(0).astype(int)
        result_df['sales_1'] = result_df['sales_1'].fillna(0).astype(int)
        result_df['sales_2'] = result_df['sales_2'].fillna(0).astype(int)
        result_df['sales_3'] = result_df['sales_3'].fillna(0).astype(int)
        result_df['sales_4'] = result_df['sales_4'].fillna(0).astype(int)
        result_df['sales_5'] = result_df.get('sales_5', pd.Series()).fillna(0).astype(int)
        # ⭐ sales_6 = sales_4 / 6（月均，基于180天/6个月）
        result_df['sales_6'] = round(result_df['sales_4'] / 6.0, 1)
        
        # ⭐ 计算当前毛利率（向量化优化）
        prezzo_vedi1 = result_df['PrezzoVedi1'].fillna(0)
        prezzo_forn = result_df['PrezzoForn'].fillna(0)
        cod_iva = result_df['CodIva'].fillna(0)
        purchase_cost = prezzo_forn * (100 + cod_iva) / 100
        result_df['current_margin_pct'] = np.where(
            prezzo_vedi1 > 0,
            np.round((prezzo_vedi1 - purchase_cost) / prezzo_vedi1 * 100, 2),
            0.0
        )
        
        # ⭐ 计算销售周期毛利率（向量化优化）
        for i in [1, 2, 3]:
            guadagno_col = f'guadagno_{i}'
            net_sales_col = f'net_sales_{i}'
            if guadagno_col in result_df.columns and net_sales_col in result_df.columns:
                result_df[f'{guadagno_col}_margin_pct'] = np.where(
                    result_df[net_sales_col] > 0,
                    np.round(result_df[guadagno_col] / result_df[net_sales_col] * 100, 2),
                    0.0
                )
        
        # Calcola differenza inventario
        result_df['inventory_diff'] = result_df['store_inventory'] - result_df['sales_1']
        
        # ⭐ 先解包 days_list（在计算前定义 days_1, days_2, days_3, days_4, days_5）
        # ⭐ days_list: [30, 60, 90, 180, period_days]
        # ⭐ 前4个用于 process_reorder_data，period_days 仅用于 UI 显示
        days_1, days_2, days_3, days_4 = days_list[0], days_list[1], days_list[2], min(days_list[3], 180)

        # ⭐ 读取原始销量数组
        sales_1 = result_df['sales_1'].values
        sales_2 = result_df['sales_2'].values
        sales_3 = result_df['sales_3'].values
        sales_4 = result_df['sales_4'].values
        store_inv = result_df['store_inventory'].values
        
        # ⭐⭐⭐ 有效销量回退机制
        # 当 ROT.30 GG (sales_1) 为零时，回退到 (90d销量 ÷ 3) 的月均值作为 30 天参考：
        #   有效日销 = (sales_3/3) / 30 = sales_3 / 90
        effective_sales = np.where(sales_1 > 0, sales_1, sales_3)
        effective_period = np.where(sales_1 > 0, days_1, days_3)
        # 标记：近期零销但有历史销售（使用了 90d/3 月均回退）
        used_fallback = (sales_1 == 0) & (sales_3 > 0)
        # 标记：真正零销售（两个周期都为零）
        truly_zero_sales = (sales_1 == 0) & (sales_3 == 0)
        
        # ⭐ 向量化计算库存天数（使用有效销量，修复除零警告）
        effective_daily_sales = np.where(effective_sales > 0, effective_sales / effective_period, 1e-10)
        result_df['inventory_days'] = np.where(
            effective_sales > 0,
            store_inv / effective_daily_sales,
            np.inf
        )
        
        # ⭐ 向量化计算销售等级（使用有效销量）
        # 【采购系统修改】30 天没销量、回退用 90 天销量时，先折算成 30 天的量再和 30 天平均比
        #   （原来直接拿 90 天总量比，等级会偏高约 3 倍）
        effective_sales_30 = np.where(sales_1 > 0, sales_1, sales_3 * days_1 / float(days_3))
        tier_a_thresh = avg_sales_30 * decision_engine.params['tier_a_threshold']
        tier_b_thresh = avg_sales_30 * decision_engine.params['tier_b_threshold']
        result_df['sales_tier'] = np.where(
            effective_sales_30 >= tier_a_thresh, 'A',
            np.where(effective_sales_30 >= tier_b_thresh, 'B', 'C')
        )
        
        # ⭐ 向量化计算趋势比率（修复除零警告）
        recent_daily = sales_1 / float(days_1)
        older_daily_raw = np.where(sales_3 != sales_1, (sales_3 - sales_1) / float(days_3 - days_1), 0)
        older_daily = np.where(older_daily_raw == 0, 1e-10, older_daily_raw)  # 避免除零
        result_df['trend_ratio'] = np.where(
            older_daily_raw != 0,
            (recent_daily - older_daily_raw) / older_daily,
            np.where(recent_daily > 0, 1.0, 0)
        )
        
        # ⭐ 向量化决策逻辑（使用 np.select）
        params = decision_engine.params
        inv_days = result_df['inventory_days'].values
        sales_tier = result_df['sales_tier'].values
        trend_ratio = result_df['trend_ratio'].values
        
        # 构建条件列表
        conditions = [
            # 🔴 紧急级别
            (inv_days < params['emergency_days']) & (sales_tier == 'A'),
            (inv_days < params['emergency_days']) & (sales_tier == 'B'),
            (inv_days < params['critical_days']) & np.isin(sales_tier, ['A', 'B']),
            (inv_days < params['urgent_days']) & (sales_tier == 'A') & (trend_ratio > params['trend_up_threshold']),
            # 🟡 建议级别
            (inv_days < params['normal_days']) & np.isin(sales_tier, ['A', 'B']),
            (inv_days < params['urgent_days']) & (trend_ratio > params['trend_up_threshold']),
            # ⭐⭐⭐ 新增：回退标记 — 近期零销但有历史销售（使用 90d/3 月均参考）
            used_fallback & (inv_days < params['normal_days']),
            # 🟢 不订购级别 — ⭐ 只标记真正零销售的产品（30天和90天都为零）
            (result_df['total_inventory'].values > 0) & truly_zero_sales,
            (inv_days > params['excess_days']) & (sales_tier == 'C'),
            (inv_days > params['normal_days']) & (trend_ratio < params['trend_down_threshold']),
        ]
        
        # 构建决策文本列表
        decision_texts = [
            '🔴【Rifornimento urgente】Esaurito in 3 giorni!',
            '🔴【Rifornimento urgente】Esaurito in 3 giorni!',
            '🔴【Ordine necessario】Inventario critico',
            '🔴【Ordine necessario】Alte vendite in crescita',
            '🟡【Ordine suggerito】Rifornimento normale',
            '🟡【Ordine suggerito】Vendite in crescita',
            '🟡【Ordine suggerito】Vendite storiche (ref. 90gg/3)',  # ⭐⭐⭐ 回退提示（90d/3 月均）
            '🟢【Non ordinare】Inventario lento',
            '🟢【Non ordinare】Inventario eccessivo',
            '🟢【Non ordinare】Vendite in calo',
        ]
        
        # 构建优先级列表
        priorities = [10, 9, 8, 7, 5, 4, 5, 1, 1, 2]
        
        # 应用决策
        result_df['decision_text'] = np.select(conditions, decision_texts, default='🟠【Osserva】Monitoraggio continuo')
        result_df['priority'] = np.select(conditions, priorities, default=3)
        
       # ⭐ 向量化计算建议订单量（性能优化版）
        # ⭐⭐⭐ 使用有效日销量（sales_1 为零时回退 (sales_3/3) 月均，即 sales_3/90 日销）
        effective_daily = effective_sales / effective_period
        
        # 1️⃣ 计算三个周期的每日销量（波动率计算用原始值）
        daily_sales_1 = sales_1 / float(days_1)
        daily_sales_2 = sales_2 / float(days_2)
        daily_sales_3 = sales_3 / float(days_3)
        
        # 2️⃣ ⭐ 向量化计算波动率（使用三个周期的日销量标准差/均值）
        mean_daily_raw = (daily_sales_1 + daily_sales_2 + daily_sales_3) / 3.0
        mean_daily = np.where(mean_daily_raw == 0, 1e-10, mean_daily_raw)  # 避免除零
        variance = ((daily_sales_1 - mean_daily)**2 + 
                   (daily_sales_2 - mean_daily)**2 + 
                   (daily_sales_3 - mean_daily)**2) / 3.0
        std_daily = np.sqrt(variance)
        volatility = np.where(mean_daily_raw > 0, std_daily / mean_daily, 0)
        
        # 3️⃣ 动态安全系数（向量化）
        high_thresh = params['volatility_high_threshold']
        min_sf = params['min_safety_factor']
        max_sf = params['max_safety_factor']
        dynamic_safety = np.where(
            volatility >= high_thresh, max_sf,
            np.where(volatility <= 0, min_sf,
                     min_sf + (max_sf - min_sf) * (volatility / high_thresh))
        )
        
        # 4️⃣ 趋势调整因子（向量化）
        weight = params['trend_weight']
        max_adj = params['max_trend_adjustment']
        adjustment = np.clip(trend_ratio * weight, -max_adj, max_adj)
        trend_factor = 1 + adjustment
        
        # 5️⃣ 目标库存（向量化）— ⭐⭐⭐ 使用有效日销量（30d 为零时回退 90d/3 月均）
        lead_time = params['lead_time_days']
        
        # 获取每个产品的 reorder_cycle（按供应商）
        if supplier_reorder_cycles and 'IDFornitore' in result_df.columns:
            # 按供应商 ID 查找各自的 reorder_cycle，找不到用全局默认
            reorder_cycles = result_df['IDFornitore'].apply(
                lambda sid: supplier_reorder_cycles.get(sid, reorder_cycle)
            ).values
        else:
            # 所有产品使用全局默认
            reorder_cycles = np.full(len(result_df), reorder_cycle)
        
        # ⭐⭐⭐ 关键修复：用 effective_daily（回退后）替代 daily_sales_30
        target_inv = effective_daily * (reorder_cycles + lead_time) * dynamic_safety * trend_factor

        # ⭐ 新增：计算 QT PER CIRCOLAZIONE = sales_4 / days_4 × reorder_cycle
        daily_sales_4_safe = np.where(sales_4 > 0, sales_4 / float(days_4), 0)
        result_df['qt_per_circolazione'] = np.round(daily_sales_4_safe * reorder_cycles, 0).astype(int)

        # 6️⃣ 原始订单量（向量化）
        # ⭐ 修复：计算时将负库存视为0，但不修改原始store_inv数据
        store_inv_for_calc = np.maximum(0, store_inv)  # 负库存按0计算
        raw_order = np.maximum(0, np.ceil(target_inv - store_inv_for_calc)).astype(int)
        
        # 7️⃣ 包装调整（向量化）（修复除零警告）
        packaging = result_df['qtasmallbox'].fillna(0).values
        packaging_safe = np.where(packaging > 1, packaging, 1)  # 避免除零
        packaging_adjusted = np.where(
            packaging > 1,
            np.ceil(raw_order / packaging_safe).astype(int) * packaging,
            raw_order
        )
        
        # 8️⃣ MOQ 调整（向量化）
        moq = params.get('moq', 1)
        result_df['suggested_order_qty'] = np.where(
            (packaging_adjusted > 0) & (packaging_adjusted < moq),
            moq,
            packaging_adjusted
        )
        
        # ⭐ 向量化转移建议
        other_inv = result_df['total_inventory'].values - store_inv
        result_df['transfer_hint'] = np.where(
            (result_df['suggested_order_qty'].values > 0) & (other_inv >= result_df['suggested_order_qty'].values),
            '📦Può trasferire ' + (other_inv.astype(int)).astype(str) + ' pezzi da altri negozi',
            ''
        )
        
        return result_df
    
    @staticmethod
    def get_summary_stats(result_df: pd.DataFrame) -> dict:
        """Ottieni riepilogo statistico（向量化优化）"""
        decision = result_df['decision_text'].values
        
        # ⭐ 修复：使用 np.vectorize 替代已移除的 np.char.contains
        # np.char.contains 在 NumPy 1.24+ 中已被移除
        contains_urgente = np.vectorize(lambda x: 'Rifornimento urgente' in x)(decision)
        contains_necessario = np.vectorize(lambda x: 'Ordine necessario' in x)(decision)
        contains_suggerito = np.vectorize(lambda x: 'Ordine suggerito' in x)(decision)
        contains_non_ordinare = np.vectorize(lambda x: 'Non ordinare' in x)(decision)
        
        emergency = np.sum(contains_urgente)
        must_order = np.sum(contains_necessario)
        suggest_order = np.sum(contains_suggerito)
        no_order = np.sum(contains_non_ordinare)
        
        return {
            'emergency_plus_must': emergency + must_order,
            'suggest_order': suggest_order,
            'no_order': no_order,
            'total': len(result_df)
        }
