#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
数据库操作模块 - 封装所有数据库查询逻辑
"""

import pandas as pd
try:
    import MySQLdb
except ImportError:          # 没装 mysqlclient 时用纯 Python 的 PyMySQL 代替
    import pymysql
    pymysql.install_as_MySQLdb()
    import MySQLdb
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
from urllib.parse import quote_plus
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from .config import DatabaseConfig
import warnings

# 抑制 pandas 的 SQLAlchemy 警告
warnings.filterwarnings('ignore', category=UserWarning, message='pandas only supports SQLAlchemy')


class DatabaseManager:
    """数据库管理器"""
    
    def __init__(self):
        self.db_config = DatabaseConfig.get_connection_params()
        # ⭐ 性能优化：SQLAlchemy engine 配置
        connection_url = f"mysql+mysqldb://{quote_plus(str(self.db_config['user']))}:{quote_plus(str(self.db_config['password']))}@{self.db_config['host']}:{self.db_config['port']}/{self.db_config['database']}?charset={self.db_config['charset']}"
        self.engine = create_engine(
            connection_url,
            # 连接池配置
            pool_size=10,           # 连接池大小（并发查询多时可增加）
            max_overflow=20,        # 允许超出 pool_size 的最大连接数
            pool_recycle=3600,      # 1 小时回收连接（防止 MySQL timeout）
            pool_pre_ping=True,     # 自动检测并恢复失效连接
            echo=False,             # 关闭 SQL 日志（调试时设为 True）
            # MySQL 特定优化
            connect_args={
                'connect_timeout': 10,  # 连接超时（秒）
                'read_timeout': 120,    # 读取超时（秒）；几家门店同时查时单条查询会慢一点，留够时间
                'write_timeout': 30,    # 写入超时（秒）
            }
        )
    
    @staticmethod
    def _build_in_placeholders(count: int) -> str:
        """
        构建 SQL IN 子句的占位符
        
        Args:
            count: 占位符数量
            
        Returns:
            例如：count=3 返回 "%, %, %"
        """
        return ', '.join(['%s'] * count)
    
    @staticmethod
    def _sanitize_int_list(values: list) -> list:
        """
        清理整数列表：转换为 int 并过滤掉 0 和负数
        
        Args:
            values: 原始值列表
            
        Returns:
            清理后的整数列表
        """
        return [int(v) for v in values if v and int(v) > 0]
    
    def get_connection(self):
        """获取数据库连接（保留兼容性）"""
        return MySQLdb.connect(**self.db_config)
    
    def get_engine(self):
        """获取 SQLAlchemy engine"""
        return self.engine
    
    # ==================== 产品提取相关查询 ====================
    
    def get_product_codes(self, store_id: int, supplier_ids: List[int], 
                         start_date: datetime, end_date: datetime) -> List[int]:
        """获取产品代码列表 - 使用参数化查询防止 SQL 注入"""
        # 输入验证
        if not supplier_ids:
            raise ValueError("supplier_ids 不能为空")
        if store_id < 0:
            raise ValueError("store_id 必须 >= 0")
        
        # 清理供应商 ID 列表
        clean_supplier_ids = self._sanitize_int_list(supplier_ids)
        if not clean_supplier_ids:
            return []
        
        sales_table = f"vendite_list_{store_id}"
        # 构建 IN 子句占位符
        placeholders = self._build_in_placeholders(len(clean_supplier_ids))
        
        query = f"""
            SELECT DISTINCT CodArticolo
            FROM {sales_table}
            WHERE Data >= %s AND Data < %s
                AND IDFornitore IN ({placeholders})
        """
        
        # 参数化查询：日期参数 + 供应商 ID 参数（结束日 +1 天，含当天）
        params = (start_date, end_date + timedelta(days=1)) + tuple(clean_supplier_ids)
        df = pd.read_sql(query, self.engine, params=params)
        return df['CodArticolo'].tolist()
    
    def get_product_sales_data(self, store_id: int, supplier_ids: List[int],
                               start_date: datetime, end_date: datetime) -> pd.DataFrame:
        """获取产品销售数据（包含完整信息）- 使用参数化查询"""
        # 输入验证
        if not supplier_ids:
            raise ValueError("supplier_ids 不能为空")
        
        # 清理供应商 ID 列表
        clean_supplier_ids = self._sanitize_int_list(supplier_ids)
        if not clean_supplier_ids:
            return pd.DataFrame()
        
        sales_table = f"vendite_list_{store_id}"
        placeholders = self._build_in_placeholders(len(clean_supplier_ids))
        
        # ⭐ 修复：只按产品分组，避免同一产品因多次销售记录产生重复行
        query = f"""
            SELECT 
                p.CodArticolo,
                p.CodBarre,
                p.Descrizione,
                p.ArticoloForn,
                SUM(v.Qta) as TotalQta,
                MAX(v.IDFornitore) as IDFornitore,  -- ⭐ 使用聚合函数获取供应商
                MIN(v.Data) as FirstSale,
                MAX(v.Data) as LastSale
            FROM {sales_table} v
            INNER JOIN prodotti p ON v.CodArticolo = p.CodArticolo
            WHERE v.Data >= %s AND v.Data < %s
                AND v.IDFornitore IN ({placeholders})
            GROUP BY p.CodArticolo  -- ⭐ 只按产品分组
            ORDER BY TotalQta DESC
        """
        
        params = (start_date, end_date + timedelta(days=1)) + tuple(clean_supplier_ids)
        return pd.read_sql(query, self.engine, params=params)
    
    # ==================== 订单建议相关查询 ====================
    
    def get_inventory_data(self, product_codes: List[int], store_id: int) -> pd.DataFrame:
        """获取库存数据 - 使用参数化查询"""
        # 输入验证
        if not product_codes:
            return pd.DataFrame(columns=['CodArticolo', 'Descrizione', 'CodBarre', 'ArticoloForn',
                                        'PrezzoForn', 'PrezzoVedi1', 'CodIva', 'qtasmallbox', 
                                        'Categoria', 'Sottocategoria', 'CategoriaNome', 'SottocategoriaNome',
                                        'store_inventory', 'total_inventory'])
        
        # 清理产品代码列表
        clean_product_codes = self._sanitize_int_list(product_codes)
        if not clean_product_codes:
            return pd.DataFrame(columns=['CodArticolo', 'Descrizione', 'CodBarre', 'ArticoloForn',
                                        'PrezzoForn', 'PrezzoVedi1', 'CodIva', 'qtasmallbox', 
                                        'Categoria', 'Sottocategoria', 'CategoriaNome', 'SottocategoriaNome',
                                        'store_inventory', 'total_inventory'])
        
        placeholders = self._build_in_placeholders(len(clean_product_codes))
        
        # ⭐ 修复：使用 DISTINCT 和 GROUP BY 确保每个产品只返回一行
        query = f"""
            SELECT DISTINCT
                p.CodArticolo,
                p.Descrizione,
                p.CodBarre,
                p.ArticoloForn,
                p.PrezzoForn AS PrezzoForn,
                p.PrezzoVedi1 AS PrezzoVedi1,
                p.CodIva AS CodIva,
                p.qtasmallbox AS qtasmallbox,
                p.Categoria AS Categoria,
                p.Sottocategoria AS Sottocategoria,
                c1.Descrizione AS CategoriaNome,
                c2.Descrizione AS SottocategoriaNome,
                COALESCE(g.G{store_id}, 0) AS store_inventory,
                COALESCE(g.G0, 0) AS Negozio0_Giacenza,
                COALESCE(g.G1, 0) AS Negozio1_Giacenza,
                COALESCE(g.G2, 0) AS Negozio2_Giacenza,
                COALESCE(g.G3, 0) AS Negozio3_Giacenza,
                COALESCE(g.G4, 0) AS Negozio4_Giacenza,
                COALESCE(g.G5, 0) AS Negozio5_Giacenza,
                COALESCE(g.G0 + g.G1 + g.G2 + g.G3 + g.G4 + g.G5, 0) AS total_inventory
            FROM prodotti p
            LEFT JOIN giacenza g ON p.CodArticolo = g.CodArticolo
            LEFT JOIN categorie c1 ON p.Categoria = c1.Categorie AND c1.SottoCategorie IS NULL
            LEFT JOIN categorie c2 ON p.Sottocategoria = c2.SottoCategorie AND p.Categoria = c2.Categorie
            WHERE p.CodArticolo IN ({placeholders})
            GROUP BY p.CodArticolo
        """
        
        # pandas read_sql 需要参数是元组
        result_df = pd.read_sql(query, self.engine, params=tuple(clean_product_codes))
        
        # ⭐ 额外去重：如果数据库中有重复记录，取第一条
        if len(result_df) > len(clean_product_codes):
            result_df = result_df.drop_duplicates(subset=['CodArticolo'], keep='first')
        
        return result_df
    
    def get_product_suppliers(self, store_id: int, product_codes: List[int], 
                             supplier_ids: Optional[List[int]] = None) -> pd.DataFrame:
        """获取产品与供应商的映射关系 - 使用参数化查询"""
        # 输入验证
        if not product_codes:
            return pd.DataFrame(columns=['CodArticolo', 'IDFornitore'])
        
        # 清理列表
        clean_product_codes = self._sanitize_int_list(product_codes)
        if not clean_product_codes:
            return pd.DataFrame(columns=['CodArticolo', 'IDFornitore'])
        
        sales_table = f"vendite_list_{store_id}"
        
        # 构建查询
        product_placeholders = self._build_in_placeholders(len(clean_product_codes))
        params = list(clean_product_codes)  # 先构建列表
        
        sql_parts = [
            "SELECT DISTINCT CodArticolo, IDFornitore",
            f"FROM {sales_table}",
            f"WHERE CodArticolo IN ({product_placeholders})"
        ]
        
        if supplier_ids:
            clean_supplier_ids = self._sanitize_int_list(supplier_ids)
            if clean_supplier_ids:
                supplier_placeholders = self._build_in_placeholders(len(clean_supplier_ids))
                sql_parts.append(f"AND IDFornitore IN ({supplier_placeholders})")
                params.extend(clean_supplier_ids)
        
        query = "\n            ".join(sql_parts)
        # pandas read_sql 需要参数是元组
        return pd.read_sql(query, self.engine, params=tuple(params))
    
    def get_sales_data(self, store_id: int, product_codes: List[int], 
                       days: int, supplier_ids: Optional[List[int]] = None) -> pd.DataFrame:
        """获取指定天数的销售数据 - 使用参数化查询"""
        # 输入验证
        if not product_codes:
            return pd.DataFrame(columns=['CodArticolo', 'sales_qty'])
        if days <= 0:
            raise ValueError("days 必须 > 0")
        
        # 清理列表
        clean_product_codes = self._sanitize_int_list(product_codes)
        if not clean_product_codes:
            return pd.DataFrame(columns=['CodArticolo', 'sales_qty'])
        
        sales_table = f"vendite_list_{store_id}"
        
        date_threshold = datetime.now().date() - timedelta(days=days)
        
        # 构建查询
        product_placeholders = self._build_in_placeholders(len(clean_product_codes))
        params = list(clean_product_codes) + [date_threshold]  # 先构建列表
        
        sql_parts = [
            "SELECT CodArticolo, SUM(Qta) AS sales_qty",
            f"FROM {sales_table}",
            f"WHERE CodArticolo IN ({product_placeholders})",
            "AND Data >= %s"
        ]
        
        if supplier_ids:
            clean_supplier_ids = self._sanitize_int_list(supplier_ids)
            if clean_supplier_ids:
                supplier_placeholders = self._build_in_placeholders(len(clean_supplier_ids))
                sql_parts.append(f"AND IDFornitore IN ({supplier_placeholders})")
                params.extend(clean_supplier_ids)
        
        query = "\n                ".join(sql_parts) + "\n                GROUP BY CodArticolo"
        
        # pandas read_sql 需要参数是元组
        return pd.read_sql(query, self.engine, params=tuple(params))
    
    def get_sales_margin_data(self, store_id: int, product_codes: List[int],
                              days: int, supplier_ids: Optional[List[int]] = None) -> pd.DataFrame:
        """获取指定天数的销售毛利数据（从销售记录）- 使用参数化查询"""
        # 输入验证
        if not product_codes:
            return pd.DataFrame(columns=['CodArticolo', 'total_guadagno', 'total_net_sales'])
        if days <= 0:
            raise ValueError("days 必须 > 0")
        
        # 清理列表
        clean_product_codes = self._sanitize_int_list(product_codes)
        if not clean_product_codes:
            return pd.DataFrame(columns=['CodArticolo', 'total_guadagno', 'total_net_sales'])
        
        sales_table = f"vendite_list_{store_id}"
        
        date_threshold = datetime.now().date() - timedelta(days=days)
        
        # 构建查询
        product_placeholders = self._build_in_placeholders(len(clean_product_codes))
        params = list(clean_product_codes) + [date_threshold]  # 先构建列表
        
        sql_parts = [
            "SELECT CodArticolo, ",
            "       SUM(Guadagno) AS total_guadagno, ",
            "       SUM(TotRighe - TotCodIva) AS total_net_sales",
            f"FROM {sales_table}",
            f"WHERE CodArticolo IN ({product_placeholders})",
            "AND Data >= %s"
        ]
        
        if supplier_ids:
            clean_supplier_ids = self._sanitize_int_list(supplier_ids)
            if clean_supplier_ids:
                supplier_placeholders = self._build_in_placeholders(len(clean_supplier_ids))
                sql_parts.append(f"AND IDFornitore IN ({supplier_placeholders})")
                params.extend(clean_supplier_ids)
        
        query = "\n            ".join(sql_parts) + "\n            GROUP BY CodArticolo"
        
        # pandas read_sql 需要参数是元组
        return pd.read_sql(query, self.engine, params=tuple(params))
    
    def get_supplier_names(self, supplier_ids: List[int]) -> Dict[int, str]:
        """获取供应商名称映射 - 使用参数化查询"""
        # 输入验证
        if not supplier_ids:
            return {}
        
        # 清理列表
        clean_supplier_ids = self._sanitize_int_list(supplier_ids)
        if not clean_supplier_ids:
            return {}
        
        placeholders = self._build_in_placeholders(len(clean_supplier_ids))
        
        query = f"""
            SELECT IDFornitore, Nome
            FROM fornitore
            WHERE IDFornitore IN ({placeholders})
        """
        
        # pandas read_sql 需要参数是元组
        df = pd.read_sql(query, self.engine, params=tuple(clean_supplier_ids))
        return dict(zip(df['IDFornitore'], df['Nome']))

    def get_all_suppliers(self) -> pd.DataFrame:
        """获取所有供应商列表（去重，按名称排序）
        屏蔽名称为 XXX 和含 FORNITORE 的供应商
        
        Returns:
            DataFrame with columns: IDFornitore, Nome
        """
        query = """
            SELECT DISTINCT IDFornitore, Nome
            FROM fornitore
            WHERE IDFornitore IS NOT NULL AND Nome IS NOT NULL
              AND TRIM(Nome) != 'XXX'
              AND Nome NOT LIKE '%%FORNITORE%%'
            ORDER BY Nome
        """
        return pd.read_sql(text(query), self.engine)

    def get_product_codes_by_suppliers(self, supplier_ids: List[int],
                                        start_date: datetime, end_date: datetime) -> List[int]:
        """根据选中的供应商获取有销售记录的产品代码列表
        
        Args:
            supplier_ids: 供应商 ID 列表
            start_date: 开始日期
            end_date: 结束日期
            
        Returns:
            产品代码列表
        """
        if not supplier_ids:
            return []
        
        clean_supplier_ids = self._sanitize_int_list(supplier_ids)
        if not clean_supplier_ids:
            return []
        
        s_date = start_date.strftime('%Y-%m-%d') if hasattr(start_date, 'strftime') else str(start_date)
        if hasattr(end_date, 'strftime'):
            e_next = (end_date + timedelta(days=1)).strftime('%Y-%m-%d')
        else:
            e_next = (datetime.strptime(str(end_date), '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d')
        
        suppliers_str = ', '.join(str(s) for s in clean_supplier_ids)
        
        # UNION 所有门店表，获取有销售的产品代码（结束日 +1 天，含当天）
        query = f"""
            SELECT DISTINCT p.CodArticolo
            FROM prodotti p
            WHERE p.IDFornitore IN ({suppliers_str})
            AND p.CodArticolo IN (
                SELECT CodArticolo FROM vendite_list_0
                WHERE Data >= '{s_date}' AND Data < '{e_next}' AND Qta > 0
                UNION
                SELECT CodArticolo FROM vendite_list_1
                WHERE Data >= '{s_date}' AND Data < '{e_next}' AND Qta > 0
                UNION
                SELECT CodArticolo FROM vendite_list_2
                WHERE Data >= '{s_date}' AND Data < '{e_next}' AND Qta > 0
                UNION
                SELECT CodArticolo FROM vendite_list_3
                WHERE Data >= '{s_date}' AND Data < '{e_next}' AND Qta > 0
                UNION
                SELECT CodArticolo FROM vendite_list_4
                WHERE Data >= '{s_date}' AND Data < '{e_next}' AND Qta > 0
            )
            ORDER BY p.CodArticolo
        """
        df = pd.read_sql(text(query), self.engine)
        return df['CodArticolo'].tolist()
    
    # ==================== 批量查询优化 ====================
    
    def query_reorder_data(self, store_id: int, product_codes: List[int],
                          days_list: List[int], 
                          supplier_ids: Optional[List[int]] = None,
                          start_date=None, end_date=None) -> Dict[str, pd.DataFrame]:
        """
        批量查询订单建议所需的所有数据（性能优化版）
        
        优化点：
        1. 合并销售数据和毛利数据查询，减少数据库往返
        2. 使用 UNION ALL 一次性获取多个周期的数据
        3. 添加 SQL 提示利用索引
        """
        if not product_codes:
            return {
                'inventory': pd.DataFrame(),
                'suppliers': pd.DataFrame(),
                'sales_1': pd.DataFrame(), 'sales_2': pd.DataFrame(), 'sales_3': pd.DataFrame(), 'sales_4': pd.DataFrame(),
                'margin_1': pd.DataFrame(), 'margin_2': pd.DataFrame(), 'margin_3': pd.DataFrame()
            }
        
        # 清理列表
        clean_product_codes = self._sanitize_int_list(product_codes)
        if not clean_product_codes:
            return {
                'inventory': pd.DataFrame(),
                'suppliers': pd.DataFrame(),
                'sales_1': pd.DataFrame(), 'sales_2': pd.DataFrame(), 'sales_3': pd.DataFrame(), 'sales_4': pd.DataFrame(),
                'margin_1': pd.DataFrame(), 'margin_2': pd.DataFrame(), 'margin_3': pd.DataFrame()
            }
        
        placeholders = self._build_in_placeholders(len(clean_product_codes))
        sales_table = f"vendite_list_{store_id}"
        
        # 1️⃣ 查询库存数据（⭐ 修复：添加 DISTINCT 和 GROUP BY 防止重复）
        inventory_query = f"""
            SELECT DISTINCT
                p.CodArticolo, p.Descrizione, p.CodBarre, p.ArticoloForn,
                p.PrezzoForn, p.PrezzoVedi1, p.CodIva, p.qtasmallbox,
                p.Categoria, p.Sottocategoria,
                c1.Descrizione AS CategoriaNome, c2.Descrizione AS SottocategoriaNome,
                COALESCE(g.G{store_id}, 0) AS store_inventory,
                COALESCE(g.G0, 0) AS Negozio0_Giacenza,
                COALESCE(g.G1, 0) AS Negozio1_Giacenza,
                COALESCE(g.G2, 0) AS Negozio2_Giacenza,
                COALESCE(g.G3, 0) AS Negozio3_Giacenza,
                COALESCE(g.G4, 0) AS Negozio4_Giacenza,
                COALESCE(g.G5, 0) AS Negozio5_Giacenza,
                COALESCE(g.G0 + g.G1 + g.G2 + g.G3 + g.G4 + g.G5, 0) AS total_inventory
            FROM prodotti p
            LEFT JOIN giacenza g ON p.CodArticolo = g.CodArticolo
            LEFT JOIN categorie c1 ON p.Categoria = c1.Categorie AND c1.SottoCategorie IS NULL
            LEFT JOIN categorie c2 ON p.Sottocategoria = c2.SottoCategorie AND p.Categoria = c2.Categorie
            WHERE p.CodArticolo IN ({placeholders})
            GROUP BY p.CodArticolo
        """
        # ⭐ 速度：下面几条查询互不相关，同时发给数据库（结果和一条条查一样）
        from concurrent.futures import ThreadPoolExecutor
        _pool = ThreadPoolExecutor(max_workers=5, thread_name_prefix="dbq")
        try:
            return self._query_reorder_parallel(_pool, inventory_query, clean_product_codes, store_id, sales_table,
                                                placeholders, days_list, supplier_ids, start_date, end_date)
        finally:
            _pool.shutdown(wait=False, cancel_futures=True)

    def _query_reorder_parallel(self, _pool, inventory_query, clean_product_codes, store_id, sales_table,
                                placeholders, days_list, supplier_ids, start_date, end_date):
        f_inv = _pool.submit(pd.read_sql, inventory_query, self.engine, params=tuple(clean_product_codes))
        f_after = _pool.submit(self.get_sales_after_last_receipt, store_id, clean_product_codes)
        f_last = _pool.submit(self.get_last_sale_date, store_id, clean_product_codes)
        result = {}
        
        # 2️⃣ 查询供应商映射
        supplier_query = f"""
            SELECT DISTINCT CodArticolo, IDFornitore
            FROM {sales_table}
            WHERE CodArticolo IN ({placeholders})
        """
        if supplier_ids:
            clean_supplier_ids = self._sanitize_int_list(supplier_ids)
            if clean_supplier_ids:
                supplier_ph = self._build_in_placeholders(len(clean_supplier_ids))
                supplier_query += f" AND IDFornitore IN ({supplier_ph})"
                f_sup = _pool.submit(pd.read_sql, supplier_query, self.engine,
                                     params=tuple(clean_product_codes + clean_supplier_ids))
            else:
                f_sup = _pool.submit(pd.read_sql, supplier_query, self.engine, params=tuple(clean_product_codes))
        else:
            f_sup = _pool.submit(pd.read_sql, supplier_query, self.engine, params=tuple(clean_product_codes))
        
        # 3️⃣ ⭐ 性能优化：合并查询所有周期的销售和毛利数据（使用 UNION ALL）
        all_sales_query_parts = []
        all_params = []
        
        for i, days in enumerate(days_list, 1):
            # ⭐ 最后一个周期 = 自定义期间(sales_5 / Neg{s}_period_sales)，
            #    用调用方传入的真实 start_date/end_date 过滤（与 get_all_stores_sales_summary 对齐）；
            #    其余周期(30/60/90/180)仍用「今天 − N 天」锚点。
            is_period = (i == len(days_list))
            query_part = f"""
                SELECT {i} AS period_index, CodArticolo, {days} AS period_days,
                       SUM(Qta) AS sales_qty,
                       SUM(Guadagno) AS total_guadagno,
                       SUM(TotRighe - TotCodIva) AS total_net_sales
                FROM {sales_table}
                WHERE CodArticolo IN ({placeholders})
            """
            # ⭐ 修复：先添加产品代码，再添加日期条件，最后添加供应商 ID
            all_params.extend(clean_product_codes)
            
            if is_period and start_date is not None and end_date is not None:
                # ⭐ Data 是 DATETIME：用「结束日 +1 天」+ `<` 才能含当天，
                #    否则 `<= end_date` 会被解释成 `<= end_date 00:00:00` 漏掉当天整天
                end_date_excl = end_date + timedelta(days=1) if hasattr(end_date, 'strftime') else end_date
                query_part += " AND Data >= %s AND Data < %s"
                all_params.extend([start_date, end_date_excl])
            else:
                date_threshold = datetime.now().date() - timedelta(days=days)
                query_part += " AND Data >= %s"
                all_params.append(date_threshold)
            
            if supplier_ids:
                clean_supplier_ids = self._sanitize_int_list(supplier_ids)
                if clean_supplier_ids:
                    supplier_ph = self._build_in_placeholders(len(clean_supplier_ids))
                    query_part += f" AND IDFornitore IN ({supplier_ph})"
                    all_params.extend(clean_supplier_ids)
            
            query_part += " GROUP BY CodArticolo"
            all_sales_query_parts.append(query_part)
        
        # 合并所有周期的查询
        combined_query = " UNION ALL ".join(all_sales_query_parts)
        combined_query = f"SELECT * FROM ({combined_query}) AS combined"
        
        # 执行合并查询（⭐ 速度：先试「销售表只翻一遍、同时算出每个周期」的写法，结果和上面 UNION ALL 一样；
        #    万一数据库不认这种写法，退回 UNION ALL）
        f_comb = _pool.submit(self._period_sales, combined_query, tuple(all_params), sales_table, placeholders,
                              clean_product_codes, days_list, supplier_ids, start_date, end_date)
        inventory_df = f_inv.result()
        # ⭐ 额外去重：如果数据库中有重复记录，取第一条
        if len(inventory_df) > len(clean_product_codes):
            inventory_df = inventory_df.drop_duplicates(subset=['CodArticolo'], keep='first')
        result['inventory'] = inventory_df
        result['suppliers'] = f_sup.result()
        combined_df = f_comb.result()
        
        # 拆分结果到各个周期（⭐ 用 period_index 序号区分，避免 period_days 值重复时串数据）
        for i, days in enumerate(days_list, 1):
            period_df = combined_df[combined_df['period_index'] == i].copy()
            result[f'sales_{i}'] = period_df[['CodArticolo', 'sales_qty']].rename(columns={'sales_qty': 'sales_qty'})
            result[f'margin_{i}'] = period_df[['CodArticolo', 'total_guadagno', 'total_net_sales']]
        
        # ⭐ 新增：获取最后一次进货后的销售件数
        result['sales_after_last_in'] = f_after.result()
        
        # ⭐ 新增：获取最后一次销售日期
        result['last_sale_date'] = f_last.result()
        
        return result
    
    SINGLE_PASS_PERIODS = True

    def _period_sales(self, union_query, union_params, sales_table, placeholders, codes, days_list,
                      supplier_ids, start_date, end_date):
        """各周期销量 / 毛利 / 净销售额，返回和 UNION ALL 查询一样的表（period_index, CodArticolo, period_days, ...）"""
        if self.SINGLE_PASS_PERIODS:
            try:
                return self._period_sales_one_pass(sales_table, placeholders, codes, days_list, supplier_ids,
                                                   start_date, end_date)
            except Exception as e:
                print(f"[WARN] 一次查完各周期销量没成功，改用原来的查询：{e}")
        return pd.read_sql(union_query, self.engine, params=union_params)

    def _period_sales_one_pass(self, sales_table, placeholders, codes, days_list, supplier_ids, start_date, end_date):
        """
        销售表只翻一遍：每个周期用 CASE WHEN 只加自己那段日期的行（SUM 里不算的是 NULL，和分开查一样）；
        某个商品在某个周期一行都没有 → 那个周期没有这一行（和分开查 GROUP BY 一样）。
        """
        conds, cond_params = [], []
        for i, days in enumerate(days_list, 1):
            is_period = (i == len(days_list))
            if is_period and start_date is not None and end_date is not None:
                end_date_excl = end_date + timedelta(days=1) if hasattr(end_date, 'strftime') else end_date
                conds.append("(Data >= %s AND Data < %s)")
                cond_params.append([start_date, end_date_excl])
            else:
                conds.append("(Data >= %s)")
                cond_params.append([datetime.now().date() - timedelta(days=days)])
        sel, params = [], []
        for i, (c, cp) in enumerate(zip(conds, cond_params), 1):
            sel.append(f"SUM(CASE WHEN {c} THEN Qta END) AS q{i}, "
                       f"SUM(CASE WHEN {c} THEN Guadagno END) AS g{i}, "
                       f"SUM(CASE WHEN {c} THEN TotRighe - TotCodIva END) AS n{i}, "
                       f"SUM(CASE WHEN {c} THEN 1 ELSE 0 END) AS k{i}")
            params += cp * 4
        query = (f"SELECT CodArticolo, {', '.join(sel)} FROM {sales_table} "
                 f"WHERE CodArticolo IN ({placeholders}) AND ({' OR '.join(conds)})")
        params += list(codes)
        for cp in cond_params:
            params += cp
        if supplier_ids:
            clean_supplier_ids = self._sanitize_int_list(supplier_ids)
            if clean_supplier_ids:
                query += f" AND IDFornitore IN ({self._build_in_placeholders(len(clean_supplier_ids))})"
                params += clean_supplier_ids
        query += " GROUP BY CodArticolo"
        wide = pd.read_sql(query, self.engine, params=tuple(params))
        parts = []
        for i, days in enumerate(days_list, 1):
            w = wide[pd.to_numeric(wide[f"k{i}"], errors="coerce").fillna(0) > 0]
            parts.append(pd.DataFrame({"period_index": i, "CodArticolo": w["CodArticolo"].values, "period_days": days,
                                       "sales_qty": w[f"q{i}"].values, "total_guadagno": w[f"g{i}"].values,
                                       "total_net_sales": w[f"n{i}"].values}))
        return pd.concat(parts, ignore_index=True)

    def get_last_positive_receipt(self, product_codes: List[int]) -> pd.DataFrame:
        """
        获取产品最后一次正数入库记录和入库时间
        
        从 caricolistalist 表中查询每个产品最后一次 Quantita > 0 的记录
        如果最后一次是负数，往前找直到找到正数
        
        Args:
            product_codes: 产品代码列表
            
        Returns:
            DataFrame 包含 CodArticolo, LastInDate, LastInQuantita
        """
        if not product_codes:
            return pd.DataFrame(columns=['CodArticolo', 'LastInDate', 'LastInQuantita'])
        
        # 清理列表
        clean_product_codes = self._sanitize_int_list(product_codes)
        if not clean_product_codes:
            return pd.DataFrame(columns=['CodArticolo', 'LastInDate', 'LastInQuantita'])
        
        try:
            # ⭐ 使用参数化查询防止 SQL 注入
            placeholders = self._build_in_placeholders(len(clean_product_codes))
            
            query = f"""
                SELECT
                    c.CodArticolo,
                    c.Cdata AS LastInDate,
                    c.Quantita AS LastInQuantita
                FROM caricolistalist c
                INNER JOIN (
                    SELECT CodArticolo, MAX(Cdata) AS LastPositiveDate
                    FROM caricolistalist
                    WHERE CodArticolo IN ({placeholders})
                        AND Quantita > 0
                    GROUP BY CodArticolo
                ) last_pos
                ON c.CodArticolo = last_pos.CodArticolo 
                AND c.Cdata = last_pos.LastPositiveDate
                WHERE c.Quantita > 0
            """
            
            # pandas read_sql 需要参数是元组
            result_df = pd.read_sql(query, self.engine, params=tuple(clean_product_codes))
            
            # ⭐ 修复：去重确保每个产品只返回一行
            # 如果同一天有多条入库记录，取数量最大的那条
            if not result_df.empty and result_df.duplicated(subset=['CodArticolo']).any():
                result_df = result_df.sort_values('LastInQuantita', ascending=False)
                result_df = result_df.drop_duplicates(subset=['CodArticolo'], keep='first')
                print(f"[INFO] get_last_positive_receipt: 去重后保留 {len(result_df)} 条记录")
            
            return result_df
            
        except MySQLdb.Error as e:
            # 数据库错误：记录日志并重新抛出
            print(f"[ERROR] 数据库查询失败：{e}")
            raise
        except Exception as e:
            # 其他错误：返回空 DataFrame
            print(f"[ERROR] 获取最后一次正数入库记录失败：{e}")
            return pd.DataFrame(columns=['CodArticolo', 'LastInDate', 'LastInQuantita'])
    
    def get_monthly_sales_for_seasonality(self, store_id: int, product_codes: List[int]) -> pd.DataFrame:
        """
        获取产品的月度销售数据，用于季节性分析
        
        返回以下月份的销售数据：
        - 去年前3个月：去年1月、2月、3月（假设当前是4月）
        - 今年前3个月：今年1月、2月、3月
        - 今年当月：今年4月
        
        Args:
            store_id: 门店ID
            product_codes: 产品代码列表
            
        Returns:
            DataFrame 包含 CodArticolo 和各月度销售数据
        """
        if not product_codes:
            return pd.DataFrame()
        
        clean_product_codes = self._sanitize_int_list(product_codes)
        if not clean_product_codes:
            return pd.DataFrame()
        
        try:
            placeholders = self._build_in_placeholders(len(clean_product_codes))
            sales_table = f"vendite_list_{store_id}"
            
            today = datetime.now().date()
            current_year = today.year
            current_month = today.month
            
            # 构建查询：按年月分组统计销售量
            query = f"""
                SELECT 
                    CodArticolo,
                    YEAR(Data) AS sale_year,
                    MONTH(Data) AS sale_month,
                    SUM(Qta) AS monthly_sales
                FROM {sales_table}
                WHERE CodArticolo IN ({placeholders})
                GROUP BY CodArticolo, YEAR(Data), MONTH(Data)
            """
            
            result_df = pd.read_sql(query, self.engine, params=tuple(clean_product_codes))
            
            if result_df.empty:
                return pd.DataFrame()
            
            # 透视数据：将年月转换为列
            result_df['year_month'] = result_df['sale_year'].astype(str) + '-' + result_df['sale_month'].astype(str).str.zfill(2)
            
            pivot_df = result_df.pivot_table(
                index='CodArticolo',
                columns='year_month',
                values='monthly_sales',
                fill_value=0
            ).reset_index()
            
            # 重命名列
            pivot_df.columns.name = None
            column_names = ['CodArticolo']
            for col in pivot_df.columns[1:]:
                column_names.append(f'sales_{col}')
            pivot_df.columns = column_names
            
            return pivot_df
            
        except Exception as e:
            print(f"[ERROR] 获取季节性销售数据失败：{e}")
            return pd.DataFrame()
    
    def get_sales_after_last_receipt(self, store_id: int, product_codes: List[int]) -> pd.DataFrame:
        """
        获取最后一次进货后的销售件数
        
        对于每个产品，查询从最后一次正数入库日期到现在的销售数量
        
        Args:
            store_id: 门店 ID
            product_codes: 产品代码列表
            
        Returns:
            DataFrame 包含 CodArticolo, SalesAfterLastIn
        """
        if not product_codes:
            return pd.DataFrame(columns=['CodArticolo', 'SalesAfterLastIn'])
        
        # 清理列表
        clean_product_codes = self._sanitize_int_list(product_codes)
        if not clean_product_codes:
            return pd.DataFrame(columns=['CodArticolo', 'SalesAfterLastIn'])
        
        try:
            placeholders = self._build_in_placeholders(len(clean_product_codes))
            sales_table = f"vendite_list_{store_id}"
            
            # ⭐ 子查询获取最后一次入库日期，然后查询该日期后的销售
            query = f"""
                SELECT 
                    s.CodArticolo,
                    COALESCE(SUM(s.Qta), 0) AS SalesAfterLastIn
                FROM {sales_table} s
                INNER JOIN (
                    SELECT CodArticolo, MAX(Cdata) AS LastInDate
                    FROM caricolistalist
                    WHERE CodArticolo IN ({placeholders})
                        AND Quantita > 0
                    GROUP BY CodArticolo
                ) last_in
                ON s.CodArticolo = last_in.CodArticolo
                AND s.Data > last_in.LastInDate
                WHERE s.CodArticolo IN ({placeholders})
                GROUP BY s.CodArticolo
            """
            
            # 参数需要重复两次（子查询和外部查询各一次）
            params = tuple(clean_product_codes + clean_product_codes)
            result_df = pd.read_sql(query, self.engine, params=params)
            
            # 对于没有入库记录的产品，返回 0
            all_products_df = pd.DataFrame({'CodArticolo': clean_product_codes})
            result_df = all_products_df.merge(result_df, on='CodArticolo', how='left')
            result_df['SalesAfterLastIn'] = result_df['SalesAfterLastIn'].fillna(0).astype(int)
            
            return result_df
            
        except Exception as e:
            print(f"[ERROR] 获取最后一次进货后销售数据失败：{e}")
            return pd.DataFrame(columns=['CodArticolo', 'SalesAfterLastIn'])
    
    def get_last_sale_date(self, store_id: int, product_codes: List[int]) -> pd.DataFrame:
        """
        获取每个产品的最后一次销售日期

        Args:
            store_id: 门店 ID
            product_codes: 产品代码列表

        Returns:
            DataFrame 包含 CodArticolo, LastSaleDate
        """
        if not product_codes:
            return pd.DataFrame(columns=['CodArticolo', 'LastSaleDate'])

        # 清理列表
        clean_product_codes = self._sanitize_int_list(product_codes)
        if not clean_product_codes:
            return pd.DataFrame(columns=['CodArticolo', 'LastSaleDate'])

        try:
            placeholders = self._build_in_placeholders(len(clean_product_codes))
            sales_table = f"vendite_list_{store_id}"

            query = f"""
                SELECT 
                    CodArticolo,
                    DATE(MAX(Data)) AS LastSaleDate
                FROM {sales_table}
                WHERE CodArticolo IN ({placeholders})
                GROUP BY CodArticolo
            """

            result_df = pd.read_sql(query, self.engine, params=tuple(clean_product_codes))

            return result_df

        except Exception as e:
            print(f"[ERROR] 获取最后销售日期失败：{e}")
            return pd.DataFrame(columns=['CodArticolo', 'LastSaleDate'])

    # ==================== Importa Excel 相关查询 ====================

    def get_product_export_info(self, product_codes: List[int]) -> pd.DataFrame:
        """
        获取导入产品的导出信息：条码、商品名、供应商

        Args:
            product_codes: 产品代码列表

        Returns:
            DataFrame 包含 CodBarre, Descrizione, IDFornitore, NomeFornitore
        """
        if not product_codes:
            return pd.DataFrame(columns=['CodBarre', 'Descrizione', 'IDFornitore', 'NomeFornitore'])

        clean_codes = self._sanitize_int_list(product_codes)
        if not clean_codes:
            return pd.DataFrame(columns=['CodBarre', 'Descrizione', 'IDFornitore', 'NomeFornitore'])

        try:
            placeholders = self._build_in_placeholders(len(clean_codes))
            query = f"""
                SELECT
                    p.CodArticolo,
                    COALESCE(p.CodBarre, '') AS CodBarre,
                    COALESCE(p.Descrizione, '') AS Descrizione,
                    COALESCE(p.IDFornitore, 0) AS IDFornitore,
                    COALESCE(f.Nome, '') AS NomeFornitore
                FROM prodotti p
                LEFT JOIN fornitore f ON p.IDFornitore = f.IDFornitore
                WHERE p.CodArticolo IN ({placeholders})
                ORDER BY p.CodArticolo
            """
            result_df = pd.read_sql(query, self.engine, params=tuple(clean_codes))
            return result_df

        except Exception as e:
            print(f"[ERROR] 获取产品导出信息失败：{e}")
            return pd.DataFrame(columns=['CodBarre', 'Descrizione', 'IDFornitore', 'NomeFornitore'])

    def get_product_ean_map(self, product_codes: List[int]) -> pd.DataFrame:
        """
        从 codbarre 表批量查询产品对应的全部条码（按 l 排序，保持原始顺序）

        Args:
            product_codes: 产品代码列表（对应 codbarre.CodArticolo）

        Returns:
            DataFrame 包含 CodArticolo, CodBarre, l
            （同一产品可能有多行条码，按 l 升序）
        """
        empty = pd.DataFrame(columns=['CodArticolo', 'CodBarre', 'l'])
        if not product_codes:
            return empty

        # 清理并去重
        clean = []
        seen = set()
        for v in product_codes:
            try:
                iv = int(v)
            except (TypeError, ValueError):
                continue
            if iv > 0 and iv not in seen:
                seen.add(iv)
                clean.append(iv)
        if not clean:
            return empty

        rows = []
        try:
            chunk_size = 2000  # 分块查询，避免 IN 子句过长
            for i in range(0, len(clean), chunk_size):
                chunk = clean[i:i + chunk_size]
                placeholders = self._build_in_placeholders(len(chunk))
                query = f"""
                    SELECT CodArticolo, CodBarre, l
                    FROM codbarre
                    WHERE CodArticolo IN ({placeholders})
                    ORDER BY CodArticolo, l
                """
                df = pd.read_sql(query, self.engine, params=tuple(chunk))
                rows.append(df)
            if rows:
                return pd.concat(rows, ignore_index=True)
            return empty
        except Exception as e:
            print(f"[ERROR] 查询 codbarre 条码失败：{e}")
            return empty

    def get_weekly_sales_by_store(self, days: int = 7) -> pd.DataFrame:
        """
        查询最近 N 天每个 negozio 的销量汇总（从 vendite_list_2026）

        Args:
            days: 查询天数，默认7天

        Returns:
            DataFrame 包含 CodArticolo, Descrizione, Negozio, TotalQta, TotalRevenue, Guadagno
        """
        try:
            date_threshold = datetime.now().date() - timedelta(days=days)

            query = """
                SELECT 
                    v.CodArticolo,
                    MAX(v.Descrizione) AS Descrizione,
                    v.Negozio,
                    SUM(v.Qta) AS TotalQta,
                    SUM(v.TotRighe) AS TotalRevenue,
                    SUM(v.Guadagno) AS TotalGuadagno
                FROM vendite_list_2026 v
                WHERE v.Data >= %s
                    AND v.CodArticolo > 0
                    AND v.Qta > 0
                GROUP BY v.CodArticolo, v.Negozio
                ORDER BY v.Negozio, TotalQta DESC
            """

            result_df = pd.read_sql(query, self.engine, params=(date_threshold,))
            return result_df

        except Exception as e:
            print(f"[ERROR] 获取近{days}天各门店销量失败：{e}")
            return pd.DataFrame(columns=['CodArticolo', 'Descrizione', 'Negozio', 'TotalQta',
                                         'TotalRevenue', 'TotalGuadagno'])

    def get_inventory_all_stores(self) -> pd.DataFrame:
        """
        查询所有门店的库存数据（从 giacenza 表）

        Returns:
            DataFrame 包含 CodArticolo, G0~G5 各门店库存, 总库存
        """
        try:
            query = """
                SELECT
                    CodArticolo,
                    COALESCE(G1, 0) AS Neg1_Inv,
                    COALESCE(G2, 0) AS Neg2_Inv,
                    COALESCE(G3, 0) AS Neg3_Inv,
                    COALESCE(G4, 0) AS Neg4_Inv,
                    COALESCE(G5, 0) AS Neg5_Inv,
                    COALESCE(G1 + G2 + G3 + G4 + G5, 0) AS TotalInventory
                FROM giacenza
                ORDER BY CodArticolo
            """

            result_df = pd.read_sql(query, self.engine)
            return result_df

        except Exception as e:
            print(f"[ERROR] 获取所有门店库存失败：{e}")
            return pd.DataFrame(columns=['CodArticolo', 'Neg1_Inv', 'Neg2_Inv', 'Neg3_Inv', 'Neg4_Inv', 'Neg5_Inv', 'TotalInventory'])

    def get_sales_and_inventory_combined(self, product_codes=None, days=7):
        """
        联合查询：最近N天各门店销量 + 当前库存

        Args:
            product_codes: 产品代码列表（可选），为None则查询所有有销售的产品
            days: 查询天数，默认7天

        Returns:
            DataFrame 包含完整信息：产品、描述、各门店销量和库存
        """
        empty_df = pd.DataFrame(columns=['CodArticolo', 'Descrizione',
                                         'Neg1_Sales', 'Neg1_Inv',
                                         'Neg2_Sales', 'Neg2_Inv',
                                         'Neg3_Sales', 'Neg3_Inv',
                                         'Neg4_Sales', 'Neg4_Inv',
                                         'Neg5_Sales', 'Neg5_Inv',
                                         'Total_Sales', 'Total_Inv'])

        try:
            date_threshold = datetime.now().date() - timedelta(days=days)

            has_product_filter = (product_codes is not None)
            if has_product_filter:
                clean_codes = self._sanitize_int_list(product_codes)
                if not clean_codes:
                    return empty_df
            else:
                clean_codes = []

            # 1. 查销量
            if has_product_filter:
                placeholders = self._build_in_placeholders(len(clean_codes))
                sales_query = f"""
                    SELECT
                        v.CodArticolo,
                        MAX(v.Descrizione) AS Descrizione,
                        v.Negozio,
                        SUM(v.Qta) AS SalesQty,
                        SUM(v.TotRighe) AS SalesRevenue,
                        SUM(v.Guadagno) AS SalesProfit
                    FROM vendite_list_2026 v
                    WHERE v.Data >= %s
                        AND v.CodArticolo IN ({placeholders})
                        AND v.Qta > 0
                    GROUP BY v.CodArticolo, v.Negozio
                """
                sales_df = pd.read_sql(sales_query, self.engine,
                                       params=tuple(clean_codes + [date_threshold]))
            else:
                sales_query = """
                    SELECT
                        v.CodArticolo,
                        MAX(v.Descrizione) AS Descrizione,
                        v.Negozio,
                        SUM(v.Qta) AS SalesQty,
                        SUM(v.TotRighe) AS SalesRevenue,
                        SUM(v.Guadagno) AS SalesProfit
                    FROM vendite_list_2026 v
                    WHERE v.Data >= %s
                        AND v.CodArticolo > 0
                        AND v.Qta > 0
                    GROUP BY v.CodArticolo, v.Negozio
                """
                sales_df = pd.read_sql(sales_query, self.engine, params=(date_threshold,))

            # 2. 查库存
            if has_product_filter:
                placeholders = self._build_in_placeholders(len(clean_codes))
                inv_query = f"""
                    SELECT
                        CodArticolo,
                        COALESCE(G1, 0) AS Neg1_Inv,
                        COALESCE(G2, 0) AS Neg2_Inv,
                        COALESCE(G3, 0) AS Neg3_Inv,
                        COALESCE(G4, 0) AS Neg4_Inv,
                        COALESCE(G5, 0) AS Neg5_Inv,
                        COALESCE(G1 + G2 + G3 + G4 + G5, 0) AS TotalInventory
                    FROM giacenza
                    WHERE CodArticolo IN ({placeholders})
                """
                inv_df = pd.read_sql(inv_query, self.engine, params=tuple(clean_codes))
            else:
                inv_df = self.get_inventory_all_stores()

            # 3. 构建销量宽表
            if sales_df.empty:
                if has_product_filter:
                    sales_pivot = pd.DataFrame({'CodArticolo': clean_codes})
                else:
                    return pd.DataFrame()
            else:
                sales_pivot = sales_df.pivot_table(
                    index='CodArticolo', columns='Negozio',
                    values='SalesQty', aggfunc='sum', fill_value=0
                ).reset_index()
                sales_pivot.columns.name = None
                col_list = ['CodArticolo']
                for col in sales_pivot.columns[1:]:
                    col_list.append('Neg{}_Sales'.format(int(col)))
                sales_pivot.columns = col_list

                if has_product_filter:
                    all_products = pd.DataFrame({'CodArticolo': clean_codes})
                    sales_pivot = all_products.merge(sales_pivot, on='CodArticolo', how='left')
                    sales_pivot = sales_pivot.fillna(0)

            # 4. 合并库存
            combined = sales_pivot.merge(inv_df, on='CodArticolo', how='left')

            # TotalInventory → Total_Inv
            combined.rename(columns={'TotalInventory': 'Total_Inv'}, inplace=True)

            sales_cols = [c for c in combined.columns if '_Sales' in c]
            combined['Total_Sales'] = combined[sales_cols].sum(axis=1) if sales_cols else 0

            # 5. 获取产品描述
            if has_product_filter:
                placeholders = self._build_in_placeholders(len(clean_codes))
                desc_query = f"""
                    SELECT CodArticolo, MAX(Descrizione) AS Descrizione
                    FROM vendite_list_2026
                    WHERE CodArticolo IN ({placeholders})
                    GROUP BY CodArticolo
                """
                desc_df = pd.read_sql(desc_query, self.engine, params=tuple(clean_codes))
            else:
                desc_query = """
                    SELECT CodArticolo, MAX(Descrizione) AS Descrizione
                    FROM vendite_list_2026
                    WHERE CodArticolo > 0
                    GROUP BY CodArticolo
                """
                desc_df = pd.read_sql(desc_query, self.engine)

            combined = combined.merge(desc_df, on='CodArticolo', how='left', suffixes=('', '_desc'))

            # 6. 选择最终列
            final_columns = ['CodArticolo', 'Descrizione']
            for i in range(1, 6):
                final_columns.extend(['Neg{}_Sales'.format(i), 'Neg{}_Inv'.format(i)])
            final_columns.extend(['Total_Sales', 'Total_Inv'])

            for col in final_columns:
                if col not in combined.columns:
                    combined[col] = 0

            if has_product_filter:
                combined['order_idx'] = combined['CodArticolo'].map(
                    {code: idx for idx, code in enumerate(clean_codes)}
                )
                combined = combined.sort_values('order_idx', na_position='last').drop(columns=['order_idx'])

            return combined[final_columns]

        except Exception as e:
            print("[ERROR] 联合查询销量+库存失败: {}".format(e))
            import traceback
            traceback.print_exc()
            return pd.DataFrame()

    # ==================== 仓库采购相关查询 ====================

    def get_annual_sales_by_store(self, year: int, product_codes: list = None) -> pd.DataFrame:
        """
        获取年度销售汇总（按门店分组）
        
        从 vendite_list_{year} 表查询各门店年销量
        
        Args:
            year: 年份（如 2026）
            product_codes: 可选产品代码过滤列表
            
        Returns:
            DataFrame: CodArticolo, 各门店年销量, 总销量
        """
        try:
            sales_table = f"vendite_list_{year}"
            
            if product_codes:
                clean_codes = self._sanitize_int_list(product_codes)
                if not clean_codes:
                    return pd.DataFrame()
                placeholders = self._build_in_placeholders(len(clean_codes))
                query = f"""
                    SELECT 
                        CodArticolo,
                        Negozio,
                        SUM(Qta) AS AnnualQty,
                        SUM(TotRighe) AS AnnualRevenue
                    FROM {sales_table}
                    WHERE CodArticolo IN ({placeholders})
                        AND Qta > 0
                    GROUP BY CodArticolo, Negozio
                    ORDER BY CodArticolo, Negozio
                """
                result_df = pd.read_sql(query, self.engine, params=tuple(clean_codes))
            else:
                query = f"""
                    SELECT 
                        CodArticolo,
                        Negozio,
                        SUM(Qta) AS AnnualQty,
                        SUM(TotRighe) AS AnnualRevenue
                    FROM {sales_table}
                    WHERE CodArticolo > 0
                        AND Qta > 0
                    GROUP BY CodArticolo, Negozio
                    ORDER BY CodArticolo, Negozio
                """
                result_df = pd.read_sql(query, self.engine)
            
            return result_df
            
        except Exception as e:
            print(f"[ERROR] 获取{year}年销售数据失败: {e}")
            return pd.DataFrame()

    def get_all_stores_sales_summary(self, product_codes: list, days_list: list = None,
                                          start_date=None, end_date=None) -> pd.DataFrame:
        """
        获取所有门店的多周期销售汇总（用于仓库采购决策）
        
        Args:
            product_codes: 产品代码列表
            days_list: 天数列表，默认 [30, 60, 90]
            start_date: 开始日期（可选，覆盖 days_list）
            end_date: 结束日期（可选，覆盖 days_list）
            
        Returns:
            DataFrame: 每个产品一行，包含各门店的多周期销量
            列: CodArticolo, Neg0_30d, Neg0_60d, Neg0_90d, Neg1_30d, ...
        """
        if not product_codes:
            return pd.DataFrame()

        clean_codes = self._sanitize_int_list(product_codes)
        if not clean_codes:
            return pd.DataFrame()

        if days_list is None:
            days_list = [30, 60, 90]

        try:
            all_rows = []
            for store_id in range(6):  # 门店 0~5
                sales_table = f"vendite_list_{store_id}"
                placeholders = self._build_in_placeholders(len(clean_codes))

                try:
                    if start_date and end_date:
                        # 使用自定义时间段（⭐ Data 是 DATETIME，结束日 +1 天 + `<` 才能含当天）
                        s_date = start_date.strftime('%Y-%m-%d') if hasattr(start_date, 'strftime') else str(start_date)
                        if hasattr(end_date, 'strftime'):
                            e_next = (end_date + timedelta(days=1)).strftime('%Y-%m-%d')
                        else:
                            e_next = (datetime.strptime(str(end_date), '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d')
                        codes_str = ', '.join(str(c) for c in clean_codes)
                        query = f"""
                            SELECT
                                CodArticolo,
                                SUM(Qta) AS SalesQty
                            FROM {sales_table}
                            WHERE CodArticolo IN ({codes_str})
                                AND Data >= '{s_date}'
                                AND Data < '{e_next}'
                                AND Qta > 0
                            GROUP BY CodArticolo
                        """
                        print(f"[DEBUG sales] 查询 {sales_table}, 日期 {s_date}~{e_next}(含当天), 产品数={len(clean_codes)}")
                        df = pd.read_sql(text(query), self.engine)
                        print(f"[DEBUG sales] {sales_table} 返回 {len(df)} 行")
                        df['Store'] = store_id
                        df['Period'] = f"{start_date}~{end_date}"
                        all_rows.append(df)
                    else:
                        for days in days_list:
                            from datetime import timedelta
                            date_threshold = datetime.now().date() - timedelta(days=days)

                            query = f"""
                                SELECT
                                    CodArticolo,
                                    SUM(Qta) AS SalesQty
                                FROM {sales_table}
                                WHERE CodArticolo IN ({placeholders})
                                    AND Data >= %s
                                    AND Qta > 0
                                GROUP BY CodArticolo
                            """
                            params = tuple(clean_codes + [date_threshold])
                            df = pd.read_sql(query, self.engine, params=params)
                            df['Store'] = store_id
                            df['Period'] = f'{days}d'
                            all_rows.append(df)

                except Exception as table_err:
                    # ⭐ 表不存在或查询失败时跳过该门店
                    print(f"[WARN sales] 跳过 {sales_table}: {table_err}")
                    continue

            if not all_rows:
                return pd.DataFrame()
            
            combined = pd.concat(all_rows, ignore_index=True)

            # 透视：行=产品，列=门店×时间段
            combined['col_name'] = combined['Store'].apply(lambda s: f'Neg{s}_') + combined['Period'].astype(str)

            pivot = combined.pivot_table(
                index='CodArticolo',
                columns='col_name',
                values='SalesQty',
                aggfunc='sum',
                fill_value=0
            ).reset_index()
            pivot.columns.name = None

            return pivot
            
        except Exception as e:
            print(f"[ERROR] 获取各门店多周期销售汇总失败: {e}")
            import traceback
            traceback.print_exc()
            return pd.DataFrame()

    def get_warehouse_purchase_data(self, product_codes: list, days_list: list = None,
                                          start_date=None, end_date=None) -> dict:
        """
        获取仓库采购所需的全量数据（一次性查询）

        包含：
        - 产品信息（描述、供应商、包装、价格）
        - 各门店当前库存（G0~G5）
        - 各门店多周期销量（30/60/90天）
        - 年度销售数据

        Args:
            product_codes: 产品代码列表
            days_list: 天数列表，默认 [30, 60, 90]
            start_date: 开始日期（可选，覆盖 days_list）
            end_date: 结束日期（可选，覆盖 days_list）

        Returns:
            dict: {
                'products': DataFrame,     # 产品信息 + 各门店库存
                'sales': DataFrame,        # 各门店多周期销量
                'annual': DataFrame        # 年度销售
            }
        """
        if not product_codes:
            return {'products': pd.DataFrame(), 'sales': pd.DataFrame(), 'annual': pd.DataFrame()}

        clean_codes = self._sanitize_int_list(product_codes)
        if not clean_codes:
            return {'products': pd.DataFrame(), 'sales': pd.DataFrame(), 'annual': pd.DataFrame()}

        result = {}

        try:
            # 1. 产品信息 + 库存
            placeholders = self._build_in_placeholders(len(clean_codes))
            product_query = f"""
                SELECT
                    p.CodArticolo,
                    p.Descrizione,
                    p.CodBarre,
                    p.ArticoloForn,
                    p.PrezzoForn,
                    p.qtasmallbox,
                    COALESCE(p.IDFornitore, 0) AS IDFornitore,
                    f.Nome AS NomeFornitore,
                    COALESCE(g.G0, 0) AS Neg0_Inv,
                    COALESCE(g.G1, 0) AS Neg1_Inv,
                    COALESCE(g.G2, 0) AS Neg2_Inv,
                    COALESCE(g.G3, 0) AS Neg3_Inv,
                    COALESCE(g.G4, 0) AS Neg4_Inv,
                    COALESCE(g.G5, 0) AS Neg5_Inv,
                    COALESCE(g.G0 + g.G1 + g.G2 + g.G3 + g.G4 + g.G5, 0) AS Total_Inv
                FROM prodotti p
                LEFT JOIN giacenza g ON p.CodArticolo = g.CodArticolo
                LEFT JOIN fornitore f ON p.IDFornitore = f.IDFornitore
                WHERE p.CodArticolo IN ({placeholders})
                ORDER BY p.IDFornitore, p.CodArticolo
            """
            result['products'] = pd.read_sql(product_query, self.engine, params=tuple(clean_codes))

            # 2. 各门店多周期销量 — 支持自定义时间段
            result['sales'] = self.get_all_stores_sales_summary(
                clean_codes, days_list, start_date, end_date
            )

            # 3. 年度销售
            current_year = datetime.now().year
            result['annual'] = self.get_annual_sales_by_store(current_year, clean_codes)

        except Exception as e:
            print(f"[ERROR] 获取仓库采购数据失败: {e}")
            import traceback
            traceback.print_exc()

        return result
