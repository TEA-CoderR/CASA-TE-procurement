"""
货物分配系统（SistemaIntegrato）的配置：订货周期、供应商分组、数据库、决策参数。
从原程序 config.py 搬过来，算法参数保持不变；去掉了界面样式和修改 LC_ALL 的部分。
"""
import os
import json
from urllib.parse import quote_plus

from settings import app_dir

SCRIPT_DIR = app_dir()
SUPPLIERS_CONFIG_FILE = os.path.join(SCRIPT_DIR, 'suppliers_config.json')


class AppConfig:
    """Configurazione applicazione"""
    
    # Configurazione finestra
    WINDOW_TITLE = "📊 Sistema Estrazione Dati + Suggerimenti Ordini"
    WINDOW_GEOMETRY = "1920x1080"
    
    # Configurazione business
    DAYS_NAMES = ["Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì"]
    QUERY_DAYS_OPTIONS = ["90", "120", "150", "180", "210", "240"]
    STORE_IDS = [0, 1, 2, 3, 4]
    
    # Opzioni ciclo di riordino (giorni)
    REORDER_CYCLE_OPTIONS = [15, 30, 45, 60, 90]
    DEFAULT_REORDER_CYCLE = 30  # Predefinito 30 giorni
    
    # Opzioni negozio
    STORE_OPTIONS = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10]  # Può essere regolato in base alla situazione reale
    DEFAULT_STORE_ID = 2  # Negozio predefinito 2 (leggerà valore dal file di configurazione tramite get_default_store_id())
    
    # Gruppi fornitori predefiniti (per giorno della settimana) - valore di backup, usato se file di configurazione non esiste o caricamento fallisce
    DEFAULT_SUPPLIER_GROUPS = {
        "Lunedì": [105, 110, 116],
        "Martedì": [113, 176, 238],
        "Mercoledì": [242, 258, 314],
        "Giovedì": [352, 425, 566],
        "Venerdì": [663, 672, 876]
    }
    
    # Opzioni giorni statistica vendite
    SALES_DAYS_OPTIONS = [15, 30, 45, 60, 90, 120, 150, 180]
    
    # Cache configurazione fornitori
    _suppliers_config = None
    
    @classmethod
    def load_suppliers_config(cls):
        """Carica file di configurazione fornitori"""
        if cls._suppliers_config is not None:
            return cls._suppliers_config
        
        try:
            if os.path.exists(SUPPLIERS_CONFIG_FILE):
                with open(SUPPLIERS_CONFIG_FILE, 'r', encoding='utf-8') as f:
                    cls._suppliers_config = json.load(f)
                print(f"[OK] File di configurazione fornitori caricato con successo: {SUPPLIERS_CONFIG_FILE}")
                return cls._suppliers_config
            else:
                print(f"[WARN] File di configurazione fornitori non esiste, usa configurazione predefinita: {SUPPLIERS_CONFIG_FILE}")
                return None
        except Exception as e:
            print(f"[WARN] Caricamento file di configurazione fornitori fallito: {e}, usa configurazione predefinita")
            return None
    
    @classmethod
    def get_supplier_groups(cls):
        """Ottieni gruppi fornitori (priorità al caricamento dal file di configurazione)"""
        config = cls.load_suppliers_config()
        if config and 'supplier_groups' in config:
            # Estrai lista ID fornitori da supplier_groups del file di configurazione
            return {
                day: data['suppliers'] 
                for day, data in config['supplier_groups'].items()
            }
        return cls.DEFAULT_SUPPLIER_GROUPS
    
    @classmethod
    def get_supplier_details(cls):
        """Ottieni dettagli fornitori"""
        config = cls.load_suppliers_config()
        if config and 'supplier_details' in config:
            return config['supplier_details']
        return {}
    
    @classmethod
    def get_default_day(cls):
        """Ottieni giorno predefinito"""
        config = cls.load_suppliers_config()
        if config and 'default_settings' in config:
            return config['default_settings'].get('default_day', 'Lunedì')
        return 'Lunedì'
    
    @classmethod
    def get_default_sales_days(cls):
        """Ottieni giorni vendite predefiniti"""
        config = cls.load_suppliers_config()
        if config and 'default_settings' in config:
            return config['default_settings'].get('default_sales_days', 90)
        return 90
    
    @classmethod
    def get_default_store_id(cls):
        """Ottieni ID negozio predefinito"""
        config = cls.load_suppliers_config()
        if config and 'default_settings' in config:
            return config['default_settings'].get('default_store_id', 2)
        return 2
    
    @classmethod
    def get_default_reorder_cycle(cls):
        """Ottieni ciclo di riordino predefinito (globale)"""
        config = cls.load_suppliers_config()
        if config and 'default_settings' in config:
            return config['default_settings'].get('default_reorder_cycle', 30)
        return 30
    
    @classmethod
    def get_supplier_reorder_cycle(cls, supplier_id, fallback=None):
        """
        Ottieni ciclo di riordino per singolo fornitore
        Priorità: supplier_details → fallback (se fornito) → default_settings → 30 giorni
        
        Args:
            supplier_id: ID del fornitore
            fallback: Valore di fallback personalizzato (es. valore selezionato dall'utente in GUI).
                     Se None, usa default_settings o 30 giorni.
        """
        supplier_id_str = str(supplier_id)
        details = cls.get_supplier_details()
        if supplier_id_str in details:
            detail_fallback = fallback if fallback is not None else cls.get_default_reorder_cycle()
            return details[supplier_id_str].get('reorder_cycle', detail_fallback)
        # ⭐ Se il fornitore non è nella config, usa il fallback dell'utente (GUI) anziché 30 fisso
        return fallback if fallback is not None else cls.get_default_reorder_cycle()


import secure_store as _secure  # noqa: E402


class DatabaseConfig:
    """Configurazione database - usa variabili d'ambiente, supporta deployment sicuro"""
    
    # Priorità variabili d'ambiente > valori predefiniti
    # Istruzioni per l'uso:
    # 1. Ambiente di sviluppo: usa valori predefiniti
    # 2. Ambiente di produzione: imposta variabili d'ambiente DB_HOST, DB_USER, DB_PASSWORD, DB_NAME
    # Esempio (Windows): set DB_PASSWORD=mysecretpassword
    # Esempio (Linux/Mac): export DB_PASSWORD=mysecretpassword
    
    HOST = os.getenv('DB_HOST', '10.8.0.69')
    PORT = int(os.getenv('DB_PORT', '3307'))
    USER = os.getenv('DB_USER', 'ysoft_readonly')   # 只读账号（程序只查询，不写数据库）
    # 内置的默认密码只以混淆形式保存（文件里看不到明文）；每台电脑在「系统设置 → 数据库连接」里输入的密码用 Windows DPAPI 加密保存
    _BOOT = 'obf:ufCRpjZ1Hisa'
    PASSWORD = os.getenv('DB_PASSWORD') or _secure.deobf(_BOOT)
    DATABASE = os.getenv('DB_NAME', 'ysoft')
    CHARSET = 'utf8mb4'
    
    @classmethod
    def configure(cls, host=None, port=None, user=None, password=None, database=None):
        """用界面设置覆盖（settings.json 的 db 部分）"""
        if host:
            cls.HOST = host
        if port:
            cls.PORT = int(port)
        if user:
            cls.USER = user
        if password is not None and password != "":
            cls.PASSWORD = _secure.unprotect(password) or cls.PASSWORD
        if database:
            cls.DATABASE = database

    @classmethod
    def get_connection_params(cls):
        """Ottieni parametri connessione database"""
        # Controlla se è stata usata la password predefinita (avviso ambiente di sviluppo)
        
        return {
            'host': cls.HOST,
            'port': cls.PORT,
            'user': cls.USER,
            'password': cls.PASSWORD,
            'database': cls.DATABASE,
            'charset': cls.CHARSET
        }
    
    @classmethod
    def is_using_default_password(cls) -> bool:
        """Controlla se usa password predefinita (per avviso sicurezza)"""
        return cls.PASSWORD == _secure.deobf(cls._BOOT) and not os.getenv('DB_PASSWORD')


class DecisionParams:
    """Configurazione parametri decisione"""
    
    # Soglie classificazione vendite
    TIER_A_THRESHOLD = 1.5  # Soglia vendite livello A (multipla rispetto alla media)
    TIER_B_THRESHOLD = 0.5  # Soglia vendite livello B
    
    # Soglie giorni inventario
    EMERGENCY_DAYS = 3      # Giorni inventario emergenza
    CRITICAL_DAYS = 7       # Giorni inventario critico
    URGENT_DAYS = 14        # Giorni inventario urgente
    NORMAL_DAYS = 30        # Giorni inventario normale
    EXCESS_DAYS = 60        # Giorni inventario eccessivo
    
    # Fattore di sicurezza e soglie trend
    SAFETY_FACTOR = 1.5     # Fattore di sicurezza base
    TREND_UP_THRESHOLD = 0.2    # Soglia trend in salita
    TREND_DOWN_THRESHOLD = -0.3  # Soglia trend in discesa
    
    # Parametri sicurezza dinamica ⭐ 最大乘积 = 1.04 × 1.25 = 1.3
    MIN_SAFETY_FACTOR = 1.0   # Fattore di sicurezza minimo (bassa volatilità)
    MAX_SAFETY_FACTOR = 1.15  # Fattore di sicurezza massimo (alta volatilità) ⭐ 原 1.2 → 1.04 → 1.15
    VOLATILITY_HIGH_THRESHOLD = 0.5  # Soglia alta volatilità (deviazione standard/media)
    
    # Parametri trend nell'ordine ⭐ 趋势因子 = 1 ± 0.25 = 0.85 ~ 1.25
    TREND_WEIGHT = 0.3        # Peso trend (0-1, più grande = più influenza)
    MAX_TREND_ADJUSTMENT = 0.15  # Massimo aggiustamento trend (15%) ⭐ 原 0.25 → 0.15
    
    # Lead time ⭐ 去掉 7 天紧急缓冲
    LEAD_TIME_DAYS = 0      # Lead time (giorni) ⭐ 原 7 → 0
    
    # Parametri calcolo confezione
    PACKING_ROUND_UP_THRESHOLD = 0.2  # Soglia trend in salita (per calcolo confezione)
    
    @classmethod
    def to_dict(cls):
        """Converti in dizionario"""
        return {
            'tier_a_threshold': cls.TIER_A_THRESHOLD,
            'tier_b_threshold': cls.TIER_B_THRESHOLD,
            'emergency_days': cls.EMERGENCY_DAYS,
            'critical_days': cls.CRITICAL_DAYS,
            'urgent_days': cls.URGENT_DAYS,
            'normal_days': cls.NORMAL_DAYS,
            'excess_days': cls.EXCESS_DAYS,
            'safety_factor': cls.SAFETY_FACTOR,
            'trend_up_threshold': cls.TREND_UP_THRESHOLD,
            'trend_down_threshold': cls.TREND_DOWN_THRESHOLD,
            'lead_time_days': cls.LEAD_TIME_DAYS,
            # 动态安全库存参数
            'min_safety_factor': cls.MIN_SAFETY_FACTOR,
            'max_safety_factor': cls.MAX_SAFETY_FACTOR,
            'volatility_high_threshold': cls.VOLATILITY_HIGH_THRESHOLD,
            # 趋势参与参数
            'trend_weight': cls.TREND_WEIGHT,
            'max_trend_adjustment': cls.MAX_TREND_ADJUSTMENT,
            # 包装计算参数
            'packing_round_up_threshold': cls.PACKING_ROUND_UP_THRESHOLD,
        }
