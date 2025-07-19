import os
import uuid
import asyncio
import sqlite3
from datetime import datetime, timedelta
import pytz
from telethon import TelegramClient, events, Button
from telethon.errors.rpcerrorlist import UserIsBlockedError
from telethon.sessions import StringSession
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from urllib.parse import urlparse
import requests
from threading import Thread
from flask import Flask
import time
import hashlib
import re
import shutil
import logging
import json
from typing import Dict, Set, Optional, Tuple, List
from telethon.tl.functions.users import GetFullUserRequest
from telethon.utils import get_display_name

# Configuração de logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('bot.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

# Reduzir logs verbosos do urllib3 e telethon
logging.getLogger('urllib3').setLevel(logging.WARNING)
logging.getLogger('telethon').setLevel(logging.WARNING)

# --- 1. CONFIGURAÇÕES ---
try:
    BOT_TOKEN = os.environ.get("BOT_TOKEN", "7369466703:AAFveJRi0cSdzwb1EUPrUGsDvhYBp1JMspM") # Token atualizado
    API_ID = int(os.environ.get("API_ID", 25317254))
    API_HASH = os.environ.get("API_HASH", "bef2f48bb6b4120c9189ecfd974eb820")
    SAO_PAULO_TZ = pytz.timezone("America/Sao_Paulo")
    DB_FILE = "./database/bot_data.db"
    RESULTS_DIR = "results"
    ADMINS_FILE = "Base/admins.txt"
    COMMISSION_RATE = 0.10
    PLAN_PRICES = {
        30: 27.00,
        60: 47.00,
        90: 67.00,
        36500: 497.00
    }
    MEU_ID = 7898948145 # ID para notificações do main.py
    BANNER_PATH = "/home/container/assets/banner_start.png"
    
    # 🚀 OTIMIZAÇÕES PARA DISPOSITIVOS MÓVEIS POTENTES (S24 ULTRA)
    # Cache mais agressivo para aproveitar a RAM abundante
    CACHE_MAX_SIZE = 200  # Aumentado para dispositivos com mais RAM
    CACHE_TTL_HOURS = 24  # Cache por 24h para melhor performance
    
    # Chunks otimizados para processadores móveis potentes
    MOBILE_CHUNK_SIZE = 250000  # Chunks maiores para Snapdragon 8 Gen 3
    MOBILE_UPDATE_FREQ = 50000  # Menos updates para economia de bateria
    
except KeyError as e:
    raise EnvironmentError(f"Missing environment variable: {e}")

try:
    client = TelegramClient("bot", API_ID, API_HASH)
    client.parse_mode = "html"
    print("✅ [INFO] Cliente Telegram criado com sucesso.")
except Exception as e:
    print(f"❌ [ERROR] Erro ao criar cliente Telegram: {e}")
    raise
scheduler = AsyncIOScheduler(timezone=SAO_PAULO_TZ)
admins_file = ADMINS_FILE
ADMIN_IDS = set()

# --- CACHE INTELIGENTE ---
class CacheInteligente:
    def __init__(self, max_size=100, ttl_hours=24):
        self.cache = {}
        self.access_count = {}
        self.cache_stats = {"hits": 0, "misses": 0, "total_requests": 0}
        self.max_size = max_size
        self.ttl_hours = ttl_hours
        self.cache_file = "./database/cache_data.json"
        self._load_cache_from_file()

    def _load_cache_from_file(self):
        """Carregar cache do arquivo ao inicializar"""
        try:
            if os.path.exists(self.cache_file):
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                # Restaurar cache
                for domain_key, cache_data in data.get('cache', {}).items():
                    timestamp_str = cache_data.get('timestamp')
                    if timestamp_str:
                        timestamp = datetime.fromisoformat(timestamp_str)
                        if not self._is_expired(timestamp):
                            self.cache[domain_key] = {
                                'results': cache_data.get('results', []),
                                'timestamp': timestamp
                            }

                # Restaurar contadores de acesso
                self.access_count = data.get('access_count', {})

                # Restaurar estatísticas (opcional)
                saved_stats = data.get('cache_stats', {})
                self.cache_stats.update(saved_stats)

                print(f"[CACHE] Cache carregado do arquivo: {len(self.cache)} domínios")
        except Exception as e:
            print(f"[CACHE ERROR] Erro ao carregar cache: {e}")

    def _save_cache_to_file(self):
        """Salvar cache no arquivo"""
        try:
            # Preparar dados para serialização
            cache_data = {}
            for domain_key, data in self.cache.items():
                cache_data[domain_key] = {
                    'results': data['results'],
                    'timestamp': data['timestamp'].isoformat()
                }

            data_to_save = {
                'cache': cache_data,
                'access_count': self.access_count,
                'cache_stats': self.cache_stats
            }

            # Criar diretório se não existir
            os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)

            # Salvar arquivo
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(data_to_save, f, ensure_ascii=False, indent=2)

            print(f"[CACHE] Cache salvo no arquivo: {len(self.cache)} domínios")
        except Exception as e:
            print(f"[CACHE ERROR] Erro ao salvar cache: {e}")

    def _is_expired(self, timestamp):
        return datetime.now() > timestamp + timedelta(hours=self.ttl_hours)

    def _cleanup_expired(self):
        """Remove entradas expiradas"""
        now = datetime.now()
        expired_keys = []
        for key, data in self.cache.items():
            if self._is_expired(data['timestamp']):
                expired_keys.append(key)

        for key in expired_keys:
            del self.cache[key]
            if key in self.access_count:
                del self.access_count[key]

    def _evict_lru(self):
        """Remove entrada menos acessada se o cache estiver cheio"""
        if len(self.cache) >= self.max_size:
            # Encontrar a chave menos acessada
            if self.access_count:
                lru_key = min(self.access_count.keys(), key=lambda k: self.access_count[k])
                del self.cache[lru_key]
                del self.access_count[lru_key]

    def get(self, domain):
        """Buscar no cache"""
        try:
            self.cache_stats["total_requests"] += 1
            domain_key = domain.lower()

            # Limpar expirados primeiro
            self._cleanup_expired()

            if domain_key in self.cache:
                cache_data = self.cache[domain_key]
                if not self._is_expired(cache_data['timestamp']):
                    # Cache hit
                    self.access_count[domain_key] = self.access_count.get(domain_key, 0) + 1
                    self.cache_stats["hits"] += 1
                    print(f"[CACHE HIT] {domain} - {len(cache_data['results'])} resultados")
                    return cache_data['results']

            # Cache miss
            self.cache_stats["misses"] += 1
            print(f"[CACHE MISS] {domain}")
            return None
        except Exception as e:
            print(f"[CACHE ERROR] Erro no get: {e}")
            return None

    def set(self, domain, results, search_completed=True):
        """Armazenar no cache apenas se a busca foi completada"""
        try:
            domain_key = domain.lower()

            # Só cachear se a busca foi completada até o final
            if not search_completed:
                print(f"[CACHE SKIP] {domain} - Busca não completada, não cacheando")
                return

            # Verificar se vale a pena cachear (só cachear se tiver resultados)
            if not results or len(results) == 0:
                print(f"[CACHE SKIP] {domain} - Sem resultados para cachear")
                return

            # Limpar expirados e fazer LRU se necessário
            self._cleanup_expired()
            self._evict_lru()

            # NÃO limitar resultados - manter todos para evitar perda de dados
            self.cache[domain_key] = {
                'results': results,  # Manter TODOS os resultados
                'timestamp': datetime.now()
            }
            self.access_count[domain_key] = 1
            print(f"[CACHE SET] {domain} - {len(results)} resultados armazenados (busca completa)")

            # Salvar cache no arquivo automaticamente
            self._save_cache_to_file()
        except Exception as e:
            print(f"[CACHE ERROR] Erro no set: {e}")

    def get_stats(self):
        """Obter estatísticas do cache"""
        try:
            total = self.cache_stats["total_requests"]
            hits = self.cache_stats["hits"]
            misses = self.cache_stats["misses"]
            hit_rate = (hits / total * 100) if total > 0 else 0

            return {
                "total_requests": total,
                "cache_hits": hits,
                "cache_misses": misses,
                "hit_rate": hit_rate,
                "cached_domains": len(self.cache),
                "cache_size": len(self.cache)
            }
        except Exception as e:
            print(f"[CACHE ERROR] Erro no get_stats: {e}")
            return {"total_requests": 0, "cache_hits": 0, "cache_misses": 0, "hit_rate": 0, "cached_domains": 0, "cache_size": 0}

    def clear(self):
        """Limpar todo o cache"""
        try:
            self.cache.clear()
            self.access_count.clear()
            self.cache_stats = {"hits": 0, "misses": 0, "total_requests": 0}
            print("[CACHE] Cache limpo completamente")

            # Salvar estado limpo no arquivo
            self._save_cache_to_file()
        except Exception as e:
            print(f"[CACHE ERROR] Erro no clear: {e}")

    def get_popular_domains(self, limit=10):
        """Obter domínios mais acessados"""
        try:
            return sorted(self.access_count.items(), key=lambda x: x[1], reverse=True)[:limit]
        except Exception as e:
            print(f"[CACHE ERROR] Erro no get_popular_domains: {e}")
            return []

# Sistema de verificação de saúde
bot_health = {
    "start_time": None,
    "is_running": False,
    "last_activity": None,
    "errors_count": 0
}

def update_bot_health(activity: str = "general"):
    """Atualizar status de saúde do bot"""
    bot_health["last_activity"] = datetime.now(SAO_PAULO_TZ)
    bot_health["is_running"] = True
    if activity == "error":
        bot_health["errors_count"] += 1

async def health_check():
    """Verificação de saúde do bot"""
    try:
        me = await client.get_me()
        uptime = datetime.now(SAO_PAULO_TZ) - bot_health["start_time"]

        health_info = {
            "bot_name": me.first_name,
            "bot_username": me.username,
            "uptime": str(uptime).split('.')[0],  # Remover microssegundos
            "is_running": bot_health["is_running"],
            "last_activity": bot_health["last_activity"].strftime("%d/%m/%Y %H:%M:%S") if bot_health["last_activity"] else "N/A",
            "errors_count": bot_health["errors_count"],
            "admins_count": len(ADMIN_IDS),
            "cache_stats": cache_inteligente.get_stats()
        }

        return health_info
    except Exception as e:
        logger.error(f"Erro no health check: {e}")
        return {"error": str(e)}

async def check_bot_ping():
    """Verificar ping e latência do bot com Telegram"""
    try:
        # Medir latência da API do Telegram
        start_time = time.time()
        me = await client.get_me()
        telegram_latency = (time.time() - start_time) * 1000  # em ms
        
        # Medir latência do banco de dados
        start_time = time.time()
        with sqlite3.connect(DB_FILE) as conn:
            conn.execute("SELECT 1")
        db_latency = (time.time() - start_time) * 1000  # em ms
        
        # Medir latência do cache
        start_time = time.time()
        cache_inteligente.get_stats()
        cache_latency = (time.time() - start_time) * 1000  # em ms
        
        # Teste de conectividade externa (API)
        start_time = time.time()
        try:
            response = requests.get("https://api.telegram.org", timeout=5)
            external_latency = (time.time() - start_time) * 1000  # em ms
            external_status = "✅ Online" if response.status_code == 200 else f"⚠️ HTTP {response.status_code}"
        except Exception:
            external_latency = None
            external_status = "❌ Offline"
        
        return {
            "telegram_latency": telegram_latency,
            "db_latency": db_latency,
            "cache_latency": cache_latency,
            "external_latency": external_latency,
            "external_status": external_status,
            "bot_username": me.username,
            "timestamp": datetime.now(SAO_PAULO_TZ)
        }
    except Exception as e:
        logger.error(f"Erro no check ping: {e}")
        return {"error": str(e)}


# Instância global do cache otimizada para S24 Ultra
cache_inteligente = CacheInteligente(max_size=CACHE_MAX_SIZE, ttl_hours=CACHE_TTL_HOURS)

# Função auxiliar para validar IDs seguros
def safe_telegram_id(value):
    """Garante que o ID está dentro dos limites seguros do Telegram API"""
    try:
        if value is None:
            return None
        value = int(value)
        if -2147483648 <= value <= 2147483647:
            return value
        return None
    except (ValueError, TypeError):
        return None

# Variáveis globais do main.py
usuarios_bloqueados: Set[int] = set()
usuarios_autorizados: Dict[int, str] = {}
mensagens_origem: Dict[int, int] = {}
urls_busca: Dict[int, str] = {}
tasks_canceladas: Dict[str, Dict[str, bool]] = {}



# Criar diretórios necessários do main.py
TEMP_DIR = "./temp_files"
os.makedirs(TEMP_DIR, exist_ok=True)
os.makedirs(RESULTS_DIR, exist_ok=True)
os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
os.makedirs(os.path.dirname(ADMINS_FILE), exist_ok=True)
# --- 2. WEB SERVER (KEEPALIVE) ---
app = Flask(__name__)
@app.route("/")
def home():
    return "I'm alive!"

@app.route("/health")
def health():
    """Endpoint de saúde do bot"""
    try:
        if bot_health["is_running"]:
            uptime = datetime.now(SAO_PAULO_TZ) - bot_health["start_time"] if bot_health["start_time"] else timedelta(0)
            return {
                "status": "healthy",
                "uptime_seconds": int(uptime.total_seconds()),
                "last_activity": bot_health["last_activity"].isoformat() if bot_health["last_activity"] else None,
                "errors_count": bot_health["errors_count"]
            }
        else:
            return {"status": "unhealthy", "reason": "Bot not running"}, 503
    except Exception as e:
        return {"status": "error", "error": str(e)}, 500

def run():
    app.run(host="0.0.0.0", port=5000)

def keep_alive():
    t = Thread(target=run)
    t.start()

# --- 3. FUNÇÕES DO BANCO DE DADOS ---

def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                first_name TEXT,
                username TEXT,
                trial_started_at TEXT,
                trial_used INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS authorizations (
                user_id INTEGER PRIMARY KEY,
                expiry_date TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS blacklist (
                user_id INTEGER PRIMARY KEY
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tokens (
                token TEXT PRIMARY KEY,
                duration_days INTEGER,
                is_used INTEGER DEFAULT 0,
                used_by INTEGER,
                used_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS referrals (
                referred_user_id INTEGER PRIMARY KEY,
                referrer_user_id INTEGER,
                registered_at TEXT,
                has_converted INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS commissions (
                commission_id INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_user_id INTEGER,
                referred_user_id INTEGER,
                token_used TEXT,
                commission_amount REAL,
                earned_at TEXT,
                is_withdrawn INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS logins (
                domain TEXT,
                login_data TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS domain_index ON logins (domain)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_logins_domain_lower ON logins (LOWER(domain))")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS withdrawal_requests (
                request_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount REAL,
                requested_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS external_apis (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                api_url TEXT UNIQUE,
                added_at TEXT,
                is_active INTEGER DEFAULT 1
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS search_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                domain TEXT,
                results_count INTEGER,
                searched_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS favorites (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                domain TEXT,
                added_at TEXT,
                UNIQUE(user_id, domain)
            )
        """)
        

    if not os.path.exists(RESULTS_DIR): 
        os.makedirs(RESULTS_DIR)

async def log_action(message: str):
    now = datetime.now(SAO_PAULO_TZ).strftime("%Y-%m-%d %H:%M:%S")
    log_message = f"[{now}] {message}\n"
    print(log_message, end='')
    with open("bot.log", "a", encoding="utf-8") as logfile: logfile.write(log_message)
def extract_domain_final(line):
    try:
        line = line.lower()
        parsed_url = urlparse(line)
        if parsed_url.netloc: return parsed_url.netloc
        domain = line.split("@")[1] if "@" in line else line
        domain = domain.split("/")[0]
        return domain
    except: return None

def add_logins_to_db(chunk: list):
    if not chunk:
        return 0

    with sqlite3.connect(DB_FILE) as conn:
        try:
            # Otimizações para inserção em massa
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.execute("PRAGMA cache_size = 10000")
            conn.execute("PRAGMA temp_store = MEMORY")

            cur = conn.cursor()

            # Usar transaction explícita para melhor performance
            cur.execute("BEGIN TRANSACTION")
            cur.executemany("INSERT OR IGNORE INTO logins (domain, login_data) VALUES (?, ?)", chunk)
            cur.execute("COMMIT")

            inserted_count = cur.rowcount
            print(f"[DB INSERT] {inserted_count:,} logins inseridos de {len(chunk):,} no chunk")
            return inserted_count

        except Exception as e:
            print(f"[DB ERROR] Erro ao inserir chunk na DB: {e}")
            try:
                cur.execute("ROLLBACK")
            except:
                pass
            return 0

def search_db(domain: str, limit: int = 50000) -> list:
    # Primeiro, tentar buscar no cache
    cached_results = cache_inteligente.get(domain)
    if cached_results is not None:
        # Retornar TODOS os resultados do cache, sem limitação
        print(f"[CACHE HIT] {domain} - {len(cached_results)} resultados do cache (sem limitação)")
        return cached_results

    # Cache miss - buscar no banco de dados com estratégia abrangente
    search_term = domain.lower()
    results = []
    
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        
        # Verificar se é busca por extensão (*.gov, *.edu, etc.)
        if domain.startswith('*.'):
            extension = domain[2:]  # Remove "*."
            query = """
                SELECT login_data 
                FROM logins 
                WHERE LOWER(domain) LIKE ?
                ORDER BY domain
                LIMIT ?
            """
            params = (f"%.{extension}", limit)
            print(f"[SMART DB] Buscando por extensão: {extension}")
            cursor.execute(query, params)
            results = [row[0] for row in cursor.fetchall()]
        else:
            # Busca ULTRA abrangente para domínios
            domain_parts = search_term.split('.')
            main_domain = domain_parts[0] if domain_parts else search_term
            
            print(f"[SMART DB] 🔍 Busca ULTRA abrangente para: {search_term}")
            
            # Usar set para evitar duplicatas
            all_results = set()
            
            # 1. Busca exata
            cursor.execute("SELECT login_data FROM logins WHERE LOWER(domain) = ?", (search_term,))
            exact_results = cursor.fetchall()
            all_results.update(row[0] for row in exact_results)
            print(f"[SMART DB] 🎯 Busca exata: {len(exact_results)} resultados")
            
            # 2. Busca por subdomínios
            cursor.execute("SELECT login_data FROM logins WHERE LOWER(domain) LIKE ?", (f"%.{search_term}",))
            subdomain_results = cursor.fetchall()
            before_sub = len(all_results)
            all_results.update(row[0] for row in subdomain_results)
            print(f"[SMART DB] 🌐 Subdomínios: +{len(all_results) - before_sub} novos (total consultado: {len(subdomain_results)})")
            
            # 3. Busca pelo nome principal
            if main_domain and len(main_domain) > 3:  # Evitar buscas muito genéricas
                cursor.execute("SELECT login_data FROM logins WHERE LOWER(domain) LIKE ? LIMIT ?", (f"%{main_domain}%", limit))
                main_results = cursor.fetchall()
                before_main = len(all_results)
                all_results.update(row[0] for row in main_results)
                print(f"[SMART DB] 🔍 Nome principal '{main_domain}': +{len(all_results) - before_main} novos (total consultado: {len(main_results)})")
            
            # 4. Para domínios gov.br, busca especial SUPER abrangente
            if 'gov.br' in search_term or 'saude.gov.br' in search_term:
                base_name = main_domain
                # Buscar variações mais amplas
                cursor.execute(
                    "SELECT login_data FROM logins WHERE LOWER(domain) LIKE ? OR LOWER(domain) LIKE ? OR LOWER(domain) LIKE ? OR LOWER(domain) LIKE ? LIMIT ?",
                    (f"%{base_name}%.gov.br", f"%{base_name}%.saude.gov.br", f"{base_name}%.gov.br", f"{base_name}%.saude.gov.br", limit)
                )
                gov_results = cursor.fetchall()
                before_gov = len(all_results)
                all_results.update(row[0] for row in gov_results)
                print(f"[SMART DB] 🏛️ Domínios governamentais: +{len(all_results) - before_gov} novos (total consultado: {len(gov_results)})")
            
            # 5. Busca adicional por partes do domínio (para sisregiii por exemplo)
            if len(main_domain) > 4:
                print(f"[SMART DB] 🔄 Busca adicional por partes do domínio '{main_domain}'...")
                for i in range(4, len(main_domain) + 1):
                    partial_domain = main_domain[:i]
                    cursor.execute("SELECT login_data FROM logins WHERE LOWER(domain) LIKE ? LIMIT ?", (f"%{partial_domain}%", 20000))
                    partial_results = cursor.fetchall()
                    before_partial = len(all_results)
                    all_results.update(row[0] for row in partial_results)
                    new_added = len(all_results) - before_partial
                    if new_added > 0:
                        print(f"[SMART DB] 🎯 Busca por '{partial_domain}': +{new_added} novos logins")
            
            results = list(all_results)

    print(f"[SMART DB] ✅ Total final ULTRA: {len(results)} logins encontrados no banco")

    # Armazenar no cache se encontrou resultados
    if results:
        cache_inteligente.set(domain, results)

    return results

def get_db_stats():
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*), COUNT(DISTINCT domain) FROM logins")
        total_logins, total_domains = cursor.fetchone()
    return total_logins or 0, total_domains or 0

def clear_logins_db():
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("DELETE FROM logins")
        conn.execute("VACUUM")

def generate_token(duration_days: int) -> str:
    token = f"PRO-{uuid.uuid4().hex[:12].upper()}"
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("INSERT INTO tokens (token, duration_days) VALUES (?, ?)", (token, duration_days))
    return token

def validate_token(token: str) -> int | None:
    with sqlite3.connect(DB_FILE) as conn:
        cur = conn.cursor()
        cur.execute("SELECT duration_days FROM tokens WHERE token = ? AND is_used = 0", (token,))
        result = cur.fetchone()
        return result[0] if result else None

def use_token(token: str, user_id: int):
    used_at = datetime.now(SAO_PAULO_TZ).strftime("%Y-%m-%d %H:%M:%S")
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("UPDATE tokens SET is_used = 1, used_by = ?, used_at = ? WHERE token = ?", (user_id, used_at, token))

def authorize_user_with_delta(user_id: int, time_delta: timedelta):
    expiry_date = datetime.now(SAO_PAULO_TZ) + time_delta
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("INSERT OR REPLACE INTO authorizations (user_id, expiry_date) VALUES (?, ?)", (user_id, expiry_date.isoformat()))
    unban_user(user_id)

def cancel_plan(user_id: int):
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("DELETE FROM authorizations WHERE user_id = ?", (user_id,))

def is_authorized(user_id: int) -> bool:
    # LIBERADO PARA TODOS - SEMPRE RETORNA TRUE
    return True

def get_trial_status(user_id: int) -> dict:
    """Verificar status do período de teste do usuário"""
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT trial_started_at, trial_used FROM users WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        
        if not result:
            return {"has_trial": False, "trial_used": False, "remaining_minutes": 0}
        
        trial_started_at, trial_used = result
        
        if trial_used:
            return {"has_trial": False, "trial_used": True, "remaining_minutes": 0}
        
        if not trial_started_at:
            return {"has_trial": False, "trial_used": False, "remaining_minutes": 0}
        
        try:
            start_time = datetime.fromisoformat(trial_started_at)
            elapsed_time = datetime.now(SAO_PAULO_TZ) - start_time
            remaining_minutes = max(0, 30 - int(elapsed_time.total_seconds() / 60))
            
            if remaining_minutes > 0:
                return {"has_trial": True, "trial_used": False, "remaining_minutes": remaining_minutes}
            else:
                # Marcar como usado se expirou
                cursor.execute("UPDATE users SET trial_used = 1 WHERE user_id = ?", (user_id,))
                conn.commit()
                return {"has_trial": False, "trial_used": True, "remaining_minutes": 0}
        except:
            return {"has_trial": False, "trial_used": False, "remaining_minutes": 0}

def start_trial(user_id: int) -> bool:
    """Iniciar período de teste para um usuário"""
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT trial_started_at, trial_used FROM users WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        
        if result and (result[0] or result[1]):
            return False  # Já usou o teste
        
        now = datetime.now(SAO_PAULO_TZ).isoformat()
        cursor.execute("""
            UPDATE users 
            SET trial_started_at = ?, trial_used = 0 
            WHERE user_id = ?
        """, (now, user_id))
        conn.commit()
        return True

def has_access(user_id: int) -> tuple[bool, str]:
    """Verificar se usuário tem acesso (autorizado, admin ou em período de teste)"""
    if user_id in ADMIN_IDS:
        return True, "admin"
    
    if is_authorized(user_id):
        return True, "authorized"
    
    trial_status = get_trial_status(user_id)
    if trial_status["has_trial"]:
        return True, "trial"
    
    return False, "none"

def get_user_expiry_date(user_id: int) -> str | None:
    with sqlite3.connect(DB_FILE) as conn:
        cur = conn.cursor()
        cur.execute("SELECT expiry_date FROM authorizations WHERE user_id = ?", (user_id,))
        result = cur.fetchone()
        if not result: return None
        try:
            expiry_dt = datetime.fromisoformat(result[0])
            if expiry_dt > datetime.now(SAO_PAULO_TZ) + timedelta(days=365*90): return "Vitalício ✨"
            return expiry_dt.strftime("%d/%m/%Y às %H:%M")
        except: return "Data inválida"

def get_admins():
    try:
        if not os.path.exists(admins_file):
            # Criar arquivo de admins se não existir
            os.makedirs(os.path.dirname(admins_file), exist_ok=True)
            with open(admins_file, "w", encoding="utf-8") as f:
                # Adicionar admin padrão
                f.write(f"{MEU_ID}\n")
            print(f"✅ [INFO] Arquivo de admins criado com admin padrão: {MEU_ID}")

        with open(admins_file, "r", encoding="utf-8") as f:
            admin_ids = set()
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if line and not line.startswith('#'):  # Ignorar comentários
                    if line.isdigit():
                        admin_ids.add(int(line))
                    else:
                        print(f"⚠️ [WARNING] Linha {line_num} inválida no arquivo de admins: {line}")

            return admin_ids

    except Exception as e:
        print(f"❌ [ERROR] Erro ao carregar admins: {e}")
        logger.error(f"Erro ao carregar admins: {e}")
        # Retornar apenas se houver admin válido no arquivo
        return {7898948145}

def add_user(user_id, first_name, username):
    with sqlite3.connect(DB_FILE) as conn:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,))
        if cur.fetchone() is None:
            pass
        cur.execute("INSERT OR REPLACE INTO users (user_id, first_name, username) VALUES (?, ?, ?)", (user_id, first_name, username))

def get_all_users_count():
    with sqlite3.connect(DB_FILE) as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(user_id) FROM users")
        return cur.fetchone()[0]

def get_banned_users_count():
    with sqlite3.connect(DB_FILE) as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(user_id) FROM blacklist")
        return cur.fetchone()[0]

def ban_user(user_id):
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("INSERT OR IGNORE INTO blacklist (user_id) VALUES (?)", (user_id,))

def unban_user(user_id):
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("DELETE FROM blacklist WHERE user_id = ?", (user_id,))

def is_banned(user_id):
    with sqlite3.connect(DB_FILE) as conn:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM blacklist WHERE user_id = ?", (user_id,))
        return cur.fetchone() is not None

def register_referral(referred_id: int, referrer_id: int):
    if referred_id == referrer_id: return
    registered_at = datetime.now(SAO_PAULO_TZ).strftime("%Y-%m-%d %H:%M:%S")
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("INSERT OR IGNORE INTO referrals (referred_user_id, referrer_user_id, registered_at) VALUES (?, ?, ?)", (referred_id, referrer_id, registered_at))

def process_conversion(referred_id: int, token_used: str, duration_days: int):
    with sqlite3.connect(DB_FILE) as conn:
        cur = conn.cursor()
        cur.execute("SELECT referrer_user_id FROM referrals WHERE referred_user_id = ? AND has_converted = 0", (referred_id,))
        result = cur.fetchone()

        if result:
            referrer_id = result[0]
            plan_price = PLAN_PRICES.get(duration_days, 0)
            commission = plan_price * COMMISSION_RATE

            if commission > 0:
                earned_at = datetime.now(SAO_PAULO_TZ).strftime("%Y-%m-%d %H:%M:%S")
                cur.execute("INSERT INTO commissions (referrer_user_id, referred_user_id, token_used, commission_amount, earned_at) VALUES (?, ?, ?, ?, ?)",
                            (referrer_id, referred_id, token_used, commission, earned_at))
                cur.execute("UPDATE referrals SET has_converted = 1 WHERE referred_user_id = ?", (referred_id,))

                asyncio.run_coroutine_threadsafe(
                    client.send_message(referrer_id, f"🎉 **Você recebeu uma comissão!**\n\nUm de seus indicados ativou um plano e você ganhou **R$ {commission:.2f}**! Use /afiliado para ver seu saldo."),
                    client.loop
                )
        conn.commit()

def get_affiliate_stats(user_id: int) -> dict:
    with sqlite3.connect(DB_FILE) as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM referrals WHERE referrer_user_id = ?", (user_id,))
        total_referrals = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM referrals WHERE referrer_user_id = ? AND has_converted = 1", (user_id,))
        total_conversions = cur.fetchone()[0]
        cur.execute("SELECT SUM(commission_amount) FROM commissions WHERE referrer_user_id = ? AND is_withdrawn = 0", (user_id,))
        current_balance = cur.fetchone()[0] or 0.0
    return {"referrals": total_referrals, "conversions": total_conversions, "earnings": current_balance}

def request_withdrawal(user_id: int, amount: float):
    requested_at = datetime.now(SAO_PAULO_TZ).strftime("%Y-%m-%d %H:%M:%S")
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("INSERT INTO withdrawal_requests (user_id, amount, requested_at) VALUES (?, ?, ?)", (user_id, amount, requested_at))

def get_unused_tokens():
    with sqlite3.connect(DB_FILE) as conn:
        cur = conn.cursor()
        cur.execute("SELECT token, duration_days FROM tokens WHERE is_used = 0")
        return cur.fetchall()

def add_search_to_history(user_id: int, domain: str, results_count: int):
    searched_at = datetime.now(SAO_PAULO_TZ).strftime("%Y-%m-%d %H:%M:%S")
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("INSERT INTO search_history (user_id, domain, results_count, searched_at) VALUES (?, ?, ?, ?)", 
                    (user_id, domain, results_count, searched_at))

def get_user_search_history(user_id: int, limit: int = 10) -> list:
    with sqlite3.connect(DB_FILE) as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT domain, results_count, searched_at 
            FROM search_history 
            WHERE user_id = ? 
            ORDER BY searched_at DESC 
            LIMIT ?
        """, (user_id, limit))
        return cur.fetchall()

def add_favorite_domain(user_id: int, domain: str):
    """Adicionar domínio aos favoritos do usuário"""
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("""
            INSERT OR IGNORE INTO favorites (user_id, domain, added_at) 
            VALUES (?, ?, ?)
        """, (user_id, domain, datetime.now(SAO_PAULO_TZ).isoformat()))

def remove_favorite_domain(user_id: int, domain: str):
    """Remover domínio dos favoritos"""
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("DELETE FROM favorites WHERE user_id = ? AND domain = ?", (user_id, domain))

def get_user_favorites(user_id: int) -> list:
    """Obter domínios favoritos do usuário"""
    with sqlite3.connect(DB_FILE) as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT domain, added_at 
            FROM favorites 
            WHERE user_id = ? 
            ORDER BY added_at DESC
        """, (user_id,))
        return cur.fetchall()

def get_domain_stats(user_id: int, domain: str) -> dict:
    """Obter estatísticas detalhadas de um domínio"""
    with sqlite3.connect(DB_FILE) as conn:
        cur = conn.cursor()
        
        # Histórico de buscas para este domínio
        cur.execute("""
            SELECT COUNT(*), SUM(results_count), MAX(searched_at), MIN(searched_at)
            FROM search_history 
            WHERE user_id = ? AND domain = ?
        """, (user_id, domain))
        search_stats = cur.fetchone()
        
        # Verificar se está nos favoritos
        cur.execute("SELECT 1 FROM favorites WHERE user_id = ? AND domain = ?", (user_id, domain))
        is_favorite = cur.fetchone() is not None
        
        return {
            'search_count': search_stats[0] or 0,
            'total_results': search_stats[1] or 0,
            'last_search': search_stats[2],
            'first_search': search_stats[3],
            'is_favorite': is_favorite
        }

def export_search_results_json(user_id: int, domain: str, results: list) -> str:
    """Exportar resultados em formato JSON"""
    import json
    
    pasta_temp = os.path.join(TEMP_DIR, str(user_id))
    os.makedirs(pasta_temp, exist_ok=True)
    
    export_data = {
        'metadata': {
            'domain': domain,
            'export_date': datetime.now(SAO_PAULO_TZ).isoformat(),
            'total_results': len(results),
            'exported_by': user_id
        },
        'results': []
    }
    
    for result in results:
        if ':' in result:
            parts = result.split(':', 1)
            export_data['results'].append({
                'email': parts[0].strip(),
                'password': parts[1].strip()
            })
    
    json_path = os.path.join(pasta_temp, f"{domain}_export.json")
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(export_data, f, indent=2, ensure_ascii=False)
    
    return json_path





# Funções do main.py
def termo_valido(termo: str) -> bool:
    if not termo or not termo.strip():
        return False
    termo = termo.strip()
    if ' ' in termo:
        return False
    padrao_url = re.compile(
        r'^(https?:\/\/)?'
        r'(?:www\.)?[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
        r'(?:\/[^\s]*)?$',
        re.IGNORECASE
    )
    return bool(padrao_url.match(termo))

def detectar_dominio_inteligente(termo: str) -> str:
    """
    Sistema inteligente para detectar domínios baseado no termo de busca
    Exemplos:
    - 'netflix' -> 'netflix.com'
    - 'google' -> 'google.com'  
    - '.gov' -> procura por domínios .gov
    - 'sisregiii' -> 'sisregiii.saude.gov.br'
    - 'facebook.com' -> 'facebook.com'
    """
    termo = termo.strip().lower()
    
    # Validar tamanho - evitar processar textos muito longos
    if len(termo) > 500:  # Limitar a 500 caracteres
        print(f"[SMART DOMAIN] Texto muito longo ({len(termo)} chars), ignorando")
        return None
    
    # Se já é um domínio válido, retorna ele mesmo
    if termo_valido(termo):
        return termo
    
    # Detectar extensões especiais
    if termo.startswith('.'):
        # Busca por extensão (.gov, .edu, .org, etc.)
        extension = termo[1:]  # Remove o ponto
        print(f"[SMART DOMAIN] Detectada busca por extensão: {extension}")
        return f"*.{extension}"  # Retorna padrão para busca por extensão
    
    # 🧠 SISTEMA INTELIGENTE AVANÇADO - Padrões governamentais e institucionais
    padroes_inteligentes = {
        # Sistemas governamentais brasileiros
        'sisreg': 'sisregiii.saude.gov.br',
        'sisregii': 'sisregiii.saude.gov.br',
        'sisregiii': 'sisregiii.saude.gov.br',
        'datasus': 'datasus.saude.gov.br',
        'cnes': 'cnes.datasus.gov.br',
        'scnes': 'scnes.saude.gov.br',
        'e-sus': 'esusab.saude.gov.br',
        'esus': 'esusab.saude.gov.br',
        'tabnet': 'tabnet.datasus.gov.br',
        'sigtap': 'sigtap.datasus.gov.br',
        'sia': 'sia.datasus.gov.br',
        'sih': 'sih.datasus.gov.br',
        'sinan': 'sinan.saude.gov.br',
        'sivep': 'sivep-gripe.saude.gov.br',
        'notivisa': 'notivisa.anvisa.gov.br',
        'anvisa': 'anvisa.gov.br',
        'crf': 'crf.org.br',
        'cfm': 'cfm.org.br',
        'cofen': 'cofen.gov.br',
        'coren': 'coren.gov.br',
        'sus': 'sus.gov.br',
        'saude': 'saude.gov.br',
        'planalto': 'planalto.gov.br',
        'presidencia': 'presidencia.gov.br',
        'tcu': 'tcu.gov.br',
        'stf': 'stf.jus.br',
        'stj': 'stj.jus.br',
        'tst': 'tst.jus.br',
        'tre': 'tre.jus.br',
        'tse': 'tse.jus.br',
        'tjsp': 'tjsp.jus.br',
        'tjrj': 'tjrj.jus.br',
        'tjmg': 'tjmg.jus.br',
        'tjrs': 'tjrs.jus.br',
        'tjpr': 'tjpr.jus.br',
        'tjsc': 'tjsc.jus.br',
        'tjba': 'tjba.jus.br',
        'tjgo': 'tjgo.jus.br',
        'tjpe': 'tjpe.jus.br',
        'tjce': 'tjce.jus.br',
        'tjpb': 'tjpb.jus.br',
        'tjal': 'tjal.jus.br',
        'tjse': 'tjse.jus.br',
        'tjrn': 'tjrn.jus.br',
        'tjpi': 'tjpi.jus.br',
        'tjma': 'tjma.jus.br',
        'tjto': 'tjto.jus.br',
        'tjpa': 'tjpa.jus.br',
        'tjap': 'tjap.jus.br',
        'tjam': 'tjam.jus.br',
        'tjrr': 'tjrr.jus.br',
        'tjac': 'tjac.jus.br',
        'tjro': 'tjro.jus.br',
        'tjmt': 'tjmt.jus.br',
        'tjms': 'tjms.jus.br',
        'tjdf': 'tjdft.jus.br',
        'tjdft': 'tjdft.jus.br',
        'tjes': 'tjes.jus.br',
        'pje': 'pje.jus.br',
        'projudi': 'projudi.tjpr.jus.br',
        'esaj': 'esaj.tjsp.jus.br',
        'tjmt': 'tjmt.jus.br',
        'detran': 'detran.gov.br',
        'denatran': 'denatran.gov.br',
        'renavam': 'renavam.denatran.gov.br',
        'cnh': 'detran.gov.br',
        'multas': 'multas.detran.gov.br',
        'ipva': 'ipva.fazenda.gov.br',
        'dpvat': 'dpvat.seguradora.com.br',
        'seguradora': 'seguradora.com.br',
        'sinesp': 'sinesp.gov.br',
        'policia': 'policia.gov.br',
        'pf': 'pf.gov.br',
        'prf': 'prf.gov.br',
        'pc': 'pc.gov.br',
        'pm': 'pm.gov.br',
        'bombeiros': 'bombeiros.gov.br',
        'samu': 'samu.gov.br',
        'sus192': 'sus192.saude.gov.br',
        'cgu': 'cgu.gov.br',
        'controladoria': 'cgu.gov.br',
        'mpf': 'mpf.mp.br',
        'mpt': 'mpt.mp.br',
        'mpe': 'mpe.mp.br',
        'dpf': 'dpf.gov.br',
        'dpu': 'dpu.def.br',
        'defensoria': 'defensoria.gov.br',
        'oab': 'oab.org.br',
        'ordem': 'oab.org.br',
        'advocacia': 'oab.org.br',
        'inss': 'inss.gov.br',
        'previdencia': 'previdencia.gov.br',
        'caixa': 'caixa.gov.br',
        'cef': 'caixa.gov.br',
        'banco': 'bb.com.br',
        'bancobrasil': 'bb.com.br',
        'bb': 'bb.com.br',
        'bndes': 'bndes.gov.br',
        'bcb': 'bcb.gov.br',
        'bacen': 'bcb.gov.br',
        'febraban': 'febraban.org.br',
        'serasa': 'serasa.com.br',
        'spc': 'spc.org.br',
        'scr': 'scr.bcb.gov.br',
        'cpf': 'cpf.receita.fazenda.gov.br',
        'cnpj': 'cnpj.receita.fazenda.gov.br',
        'receita': 'receita.fazenda.gov.br',
        'rfb': 'receita.fazenda.gov.br',
        'fazenda': 'fazenda.gov.br',
        'sefaz': 'sefaz.gov.br',
        'nfe': 'nfe.fazenda.gov.br',
        'nfce': 'nfce.fazenda.gov.br',
        'nfse': 'nfse.gov.br',
        'simples': 'simples.receita.fazenda.gov.br',
        'mei': 'mei.receita.fazenda.gov.br',
        'ecac': 'ecac.receita.fazenda.gov.br',
        'irpf': 'irpf.receita.fazenda.gov.br',
        'dimob': 'dimob.receita.fazenda.gov.br',
        'dirf': 'dirf.receita.fazenda.gov.br',
        'caged': 'caged.mte.gov.br',
        'mte': 'mte.gov.br',
        'trabalho': 'mte.gov.br',
        'emprego': 'mte.gov.br',
        'sine': 'sine.mte.gov.br',
        'fgts': 'fgts.caixa.gov.br',
        'pis': 'pis.caixa.gov.br',
        'pasep': 'pasep.bb.com.br',
        'rais': 'rais.mte.gov.br',
        'esocial': 'esocial.receita.fazenda.gov.br',
        'sped': 'sped.receita.fazenda.gov.br',
        'redesim': 'redesim.gov.br',
        'jucerja': 'jucerja.rj.gov.br',
        'jucesp': 'jucesp.sp.gov.br',
        'jucemg': 'jucemg.mg.gov.br',
        'jucepar': 'jucepar.pr.gov.br',
        'jucesc': 'jucesc.sc.gov.br',
        'jucergs': 'jucergs.rs.gov.br',
        'juceb': 'juceb.ba.gov.br',
        'juceg': 'juceg.go.gov.br',
        'jucer': 'jucer.pe.gov.br',
        'jucetins': 'jucetins.to.gov.br',
        'jucema': 'jucema.ma.gov.br',
        'juceal': 'juceal.al.gov.br',
        'jucese': 'jucese.se.gov.br',
        'jucern': 'jucern.rn.gov.br',
        'jucerp': 'jucerp.pb.gov.br',
        'jucec': 'jucec.ce.gov.br',
        'jucepi': 'jucepi.pi.gov.br',
        'jucees': 'jucees.es.gov.br',
        'jucemt': 'jucemt.mt.gov.br',
        'jucems': 'jucems.ms.gov.br',
        'jucetins': 'jucetins.to.gov.br',
    }
    
    # Banco de dados de domínios conhecidos
    dominios_conhecidos = {
        # Redes sociais
        'facebook': 'facebook.com',
        'fb': 'facebook.com',
        'instagram': 'instagram.com',
        'insta': 'instagram.com',
        'twitter': 'twitter.com',
        'x': 'twitter.com',
        'linkedin': 'linkedin.com',
        'tiktok': 'tiktok.com',
        'snapchat': 'snapchat.com',
        'youtube': 'youtube.com',
        'yt': 'youtube.com',
        'pinterest': 'pinterest.com',
        'reddit': 'reddit.com',
        'discord': 'discord.com',
        'telegram': 'telegram.org',
        'whatsapp': 'whatsapp.com',
        
        # Tecnologia
        'google': 'google.com',
        'gmail': 'gmail.com',
        'yahoo': 'yahoo.com',
        'hotmail': 'hotmail.com',
        'outlook': 'outlook.com',
        'microsoft': 'microsoft.com',
        'apple': 'apple.com',
        'icloud': 'icloud.com',
        'github': 'github.com',
        'amazon': 'amazon.com',
        'aws': 'amazonaws.com',
        'dropbox': 'dropbox.com',
        'zoom': 'zoom.us',
        'skype': 'skype.com',
        'adobe': 'adobe.com',
        'oracle': 'oracle.com',
        'salesforce': 'salesforce.com',
        
        # Streaming
        'netflix': 'netflix.com',
        'prime': 'primevideo.com',
        'disney': 'disneyplus.com',
        'hulu': 'hulu.com',
        'spotify': 'spotify.com',
        'twitch': 'twitch.tv',
        'steam': 'steampowered.com',
        'epic': 'epicgames.com',
        'xbox': 'xbox.com',
        'playstation': 'playstation.com',
        'sony': 'sony.com',
        'nintendo': 'nintendo.com',
        
        # E-commerce
        'ebay': 'ebay.com',
        'alibaba': 'alibaba.com',
        'aliexpress': 'aliexpress.com',
        'mercadolivre': 'mercadolivre.com.br',
        'shopify': 'shopify.com',
        'paypal': 'paypal.com',
        'stripe': 'stripe.com',
        
        # Bancos e financeiras
        'nubank': 'nubank.com.br',
        'itau': 'itau.com.br',
        'bradesco': 'bradesco.com.br',
        'santander': 'santander.com.br',
        'btc': 'bitcoin.org',
        'binance': 'binance.com',
        'coinbase': 'coinbase.com',
        
        # Governo e educação
        'gov': 'gov.br',
        'usp': 'usp.br',
        'unicamp': 'unicamp.br',
        'ufrj': 'ufrj.br',
        'ufmg': 'ufmg.br',
        'mit': 'mit.edu',
        'harvard': 'harvard.edu',
        'stanford': 'stanford.edu',
        
        # Outros
        'wikipedia': 'wikipedia.org',
        'bing': 'bing.com',
        'duckduckgo': 'duckduckgo.com',
        'cloudflare': 'cloudflare.com',
        'uber': 'uber.com',
        'airbnb': 'airbnb.com',
        'booking': 'booking.com',
        'tripadvisor': 'tripadvisor.com'
    }
    
    # 🚀 PRIORIDADE 1: Verificar padrões inteligentes primeiro (governamentais/institucionais)
    if termo in padroes_inteligentes:
        dominio_encontrado = padroes_inteligentes[termo]
        print(f"[SMART DOMAIN] 🧠 Padrão inteligente detectado: '{termo}' -> '{dominio_encontrado}'")
        return dominio_encontrado
    
    # 🔍 PRIORIDADE 2: Buscar correspondências parciais nos padrões inteligentes
    for key, domain in padroes_inteligentes.items():
        if termo in key or key in termo:
            print(f"[SMART DOMAIN] 🧠 Correspondência parcial inteligente: '{termo}' -> '{domain}'")
            return domain
    
    # 🌐 PRIORIDADE 3: Buscar no banco de domínios conhecidos
    if termo in dominios_conhecidos:
        dominio_encontrado = dominios_conhecidos[termo]
        print(f"[SMART DOMAIN] '{termo}' identificado como '{dominio_encontrado}'")
        return dominio_encontrado
    
    # 📝 PRIORIDADE 4: Buscar correspondências parciais
    for key, domain in dominios_conhecidos.items():
        if termo in key or key in termo:
            print(f"[SMART DOMAIN] Correspondência parcial: '{termo}' -> '{domain}'")
            return domain
    
    # ⚡ PRIORIDADE 5: Detecção de padrões governamentais por sufixo
    if any(governo in termo for governo in ['gov', 'jus', 'leg', 'mp', 'tc', 'df', 'sp', 'rj', 'mg', 'rs', 'pr', 'sc', 'ba', 'go', 'pe', 'ce', 'pb', 'al', 'se', 'rn', 'pi', 'ma', 'to', 'pa', 'ap', 'am', 'rr', 'ac', 'ro', 'mt', 'ms', 'es']):
        # Tentar domínio governamental
        if '.gov.br' not in termo:
            dominio_tentativa = f"{termo}.gov.br"
            print(f"[SMART DOMAIN] 🏛️ Padrão governamental detectado: '{termo}' -> '{dominio_tentativa}'")
            return dominio_tentativa
    
    # 🎓 PRIORIDADE 6: Detecção de padrões educacionais
    if any(edu in termo for edu in ['ufsc', 'ufpr', 'ufmg', 'ufrj', 'usp', 'unicamp', 'unesp', 'puc', 'unb', 'ufba', 'ufpe', 'ufc', 'ufpb', 'ufal', 'ufs', 'ufrn', 'ufpi', 'ufma', 'uft', 'ufpa', 'ufap', 'ufam', 'ufrr', 'ufac', 'unir', 'ufmt', 'ufms', 'ufes', 'if', 'cefet', 'fatec', 'etec']):
        # Tentar domínio educacional
        if '.edu.br' not in termo and '.br' not in termo:
            dominio_tentativa = f"{termo}.br"
            print(f"[SMART DOMAIN] 🎓 Padrão educacional detectado: '{termo}' -> '{dominio_tentativa}'")
            return dominio_tentativa
    
    # 🏥 PRIORIDADE 7: Detecção de padrões de saúde
    if any(saude in termo for saude in ['saude', 'sus', 'datasus', 'anvisa', 'fiocruz', 'inca', 'hc', 'hospital', 'clinica', 'posto', 'upa', 'samu', 'crf', 'cfm', 'cofen', 'coren']):
        # Tentar domínio de saúde
        if '.gov.br' not in termo and '.org.br' not in termo:
            if any(org in termo for org in ['crf', 'cfm', 'cofen', 'coren']):
                dominio_tentativa = f"{termo}.org.br"
                print(f"[SMART DOMAIN] 🏥 Padrão de saúde (org) detectado: '{termo}' -> '{dominio_tentativa}'")
                return dominio_tentativa
            else:
                dominio_tentativa = f"{termo}.saude.gov.br"
                print(f"[SMART DOMAIN] 🏥 Padrão de saúde (gov) detectado: '{termo}' -> '{dominio_tentativa}'")
                return dominio_tentativa
    
    # 💼 PRIORIDADE 8: Se não encontrou, tentar adicionar .com
    if '.' not in termo and len(termo) > 2:
        dominio_tentativa = f"{termo}.com"
        print(f"[SMART DOMAIN] Tentativa automática: '{termo}' -> '{dominio_tentativa}'")
        return dominio_tentativa
    
    # 🔎 ÚLTIMO RECURSO: Verificar se é um domínio válido
    if termo_valido(termo):
        return termo
    
    return None

try:
    from relatorio_premium import RelatorioPremium
except ImportError:
    class RelatorioPremium:
        def __init__(self, nome: str, user_id: int, data: str, url: str, quantidade: int):
            self.nome = nome
            self.user_id = user_id
            self.data = data
            self.url = url
            self.quantidade = quantidade
            self.imagem = None

        def criar_degradê(self): pass
        def criar_card(self): pass
        def desenhar_conteudo(self): pass
        def desenhar_logo(self): pass
        def gerar_relatorio(self): pass

# Importar a classe LoginSearch do arquivo logins_search.py
try:
    from logins_search import LoginSearch
    print("✅ [INFO] LoginSearch importado com sucesso do arquivo logins_search.py")
except ImportError as e:
    print(f"❌ [ERROR] Erro ao importar LoginSearch: {e}")
    raise



# --- 5. TAREFAS AGENDADAS ---
async def check_expirations():
    print("⏰ [SCHEDULER] Executando verificação de expiração de planos...")
    now = datetime.now(SAO_PAULO_TZ)
    threshold = now + timedelta(days=3)

    with sqlite3.connect(DB_FILE) as conn:
        cur = conn.cursor()
        cur.execute("SELECT user_id, expiry_date FROM authorizations")
        expiring_users = []
        for user_id, expiry_iso in cur.fetchall():
            try:
                expiry_date = datetime.fromisoformat(expiry_iso)
                if expiry_date > datetime.now(SAO_PAULO_TZ) + timedelta(days=365*90): continue
                if now < expiry_date < threshold:
                    days_left = (expiry_date - now).days
                    expiring_users.append((user_id, days_left + 1))
            except: continue

    for user_id, days_left in expiring_users:
        try:
            message = f"⏳ **Alerta de Expiração!**\n\nOlá! Seu plano de acesso expira em aproximadamente **{days_left} dia(s)**.\n\nClique no botão abaixo para falar com o suporte e renovar seu plano!"
            await client.send_message(user_id, message, buttons=[Button.url("✅ Renovar Agora", "https://t.me/Olhosdecristo")])
            await log_action(f"Notificação de expiração enviada para o usuário `{user_id}`.")
        except (UserIsBlockedError, ValueError): pass
        await asyncio.sleep(1)
    print("✅ [SCHEDULER] Verificação de expiração concluída.")

def save_cache_periodically():
    """Salvar cache periodicamente"""
    try:
        cache_inteligente._save_cache_to_file()
        print("⏰ [SCHEDULER] Cache salvo periodicamente")
    except Exception as e:
        print(f"⏰ [SCHEDULER ERROR] Erro ao salvar cache: {e}")

def cleanup_cache_periodically():
    """Limpeza periódica do cache"""
    try:
        cache_inteligente._cleanup_expired()
        cache_inteligente._save_cache_to_file()
        print("⏰ [SCHEDULER] Limpeza periódica do cache executada")
    except Exception as e:
        print(f"⏰ [SCHEDULER ERROR] Erro na limpeza do cache: {e}")

# --- 6. HANDLERS E MENSAGENS ---
async def send_start_message(event_or_user, referral_code=None, admin_view=True):
    """Função auxiliar para enviar a mensagem de start"""
    if hasattr(event_or_user, 'get_sender'):
        user = await event_or_user.get_sender()
        respond_method = event_or_user.respond
        # Para callbacks, usar edit se disponível
        is_callback = hasattr(event_or_user, 'edit') and hasattr(event_or_user, 'data')
    else:
        user = event_or_user
        respond_method = lambda *args, **kwargs: client.send_message(user.id, *args, **kwargs)
        is_callback = False

    add_user(user.id, user.first_name, user.username)

    if referral_code and referral_code.startswith(" ref"):
        try:
            referrer_id = int(referral_code.split("ref")[1])
            if user.id != referrer_id:
                register_referral(user.id, referrer_id)
                await respond_method("✅ Bem-vindo(a)! Sua indicação foi registrada com sucesso.")
        except (ValueError, IndexError): 
            pass

    if is_banned(user.id):
        message = f"🚫 Acesso Bloqueado, {user.first_name}\n\n🆔 Seu ID: {user.id}\n❌ Você foi banido do sistema"
        if is_callback:
            await event_or_user.edit(message)
        else:
            await respond_method(message)
        return

    if user.id in ADMIN_IDS and admin_view:
        admin_buttons = [
            [Button.inline("🔑 Gerar Token", b"gen_token_panel"), Button.inline("📢 Broadcast", b"broadcast_panel")],
            [Button.inline("📊 Estatísticas", b"stats"), Button.inline("🧠 Cache", b"cache_panel")],
            [Button.inline("🏓 Ping & Latência", b"ping_panel"), Button.inline("🛡️ Auditoria", b"audit")],
            [Button.inline("👥 Export Users", b"export_users"), Button.inline("🗑️ Limpar DB", b"clear_db_prompt")],
            [Button.inline("📖 Ver Comandos", b"show_admin_commands"), Button.inline("👤 Modo Membro", b"back_to_member_start")]
        ]
        message = f"⚙️ 𝗣𝗮𝗶𝗻𝗲𝗹 𝗱𝗲 𝗔𝗱𝗺𝗶𝗻𝗶𝘀𝘁𝗿𝗮𝗰̧𝗮̃𝗼\n\n👋 Olá, {user.first_name}!\n🆔 Seu ID: {user.id}\n👑 Seu plano: Administrador\n\n📋 Selecione uma opção:\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n💻 By: Tequ1la"
        if is_callback:
            await event_or_user.edit(message, buttons=admin_buttons)
        else:
            await respond_method(message, buttons=admin_buttons)
    else:
        # Verificar se tem acesso (autorizado, admin ou teste)
        has_user_access, access_type = has_access(user.id)
        
        if has_user_access:
            # Menu completo para usuários com acesso
            member_buttons = [
                [Button.inline("🔍 Nova Busca", b"prompt_search"), Button.inline("⭐ Favoritos", b"show_favorites")],
                [Button.inline("📜 Histórico Buscas", b"my_history"), Button.inline("💎 Planos para Grupos", b"group_plans")],
                [Button.inline("💼 Painel de Afiliado", b"affiliate_panel"), Button.inline("ℹ️ Detalhes do Acesso", b"my_access")],
                [Button.inline("❓ Ajuda", b"help_member"), Button.url("💬 Suporte", "https://t.me/Tequ1ladoxxado")]
            ]
            
            if access_type == "trial":
                trial_status = get_trial_status(user.id)
                status_text = f"🆓 TESTE GRATUITO - {trial_status['remaining_minutes']} min restantes"
                message = (
                    f"🎉 𝗕𝗲𝗺-𝘃𝗶𝗻𝗱𝗼(𝗮), {user.first_name}!\n\n"
                    f"✨ Bem-vindo ao período de teste!\n\n"
                    f"🆔 Seu ID: {user.id}\n"
                    f"📅 Status: {status_text}\n\n"
                    "📱 Aproveite para testar todas as funcionalidades:\n\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    "💻 By: Tequ1la"
                )
            else:
                message = (
                    f"🎉 𝗕𝗲𝗺-𝘃𝗶𝗻𝗱𝗼(𝗮), {user.first_name}!\n\n"
                    f"✨ Bem-vindo ao sistema LIBERADO para todos!\n\n"
                    f"🆔 Seu ID: {user.id}\n"
                    f"📅 Status: ✅ ACESSO TOTAL LIBERADO\n\n"
                    "📱 Use todos os comandos livremente:\n\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    "💻 By: Tequ1la"
                )
        else:
            # Menu para visitantes sem acesso
            trial_status = get_trial_status(user.id)
            visitor_buttons = [
                [Button.inline("🆓 Teste GRÁTIS (30min)", b"start_trial"), Button.inline("💎 Planos Premium", b"group_plans")],
                [Button.inline("🔑 Resgatar Token", b"redeem_token_prompt"), Button.inline("💼 Painel de Afiliado", b"affiliate_panel")],
                [Button.inline("❓ Ajuda", b"help_visitor"), Button.url("💬 Suporte", "https://t.me/Tequ1ladoxxado")]
            ]
            
            if trial_status["trial_used"]:
                trial_info = "🚫 Teste já utilizado"
                visitor_buttons[0][0] = Button.inline("🚫 Teste Usado", b"trial_used_info")
            else:
                trial_info = "🆓 Teste disponível (30 min)"
            
            message = (
                f"👋 𝗢𝗹𝗮́, {user.first_name}!\n\n"
                f"🎯 Bem-vindo ao melhor sistema de busca!\n\n"
                f"🆔 Seu ID: {user.id}\n"
                f"📅 Status: {trial_info}\n\n"
                "🚀 **Opções disponíveis:**\n"
                "• 🆓 Teste grátis de 30 minutos\n"
                "• 💎 Planos premium com acesso total\n"
                "• 🔑 Resgatar token se já possui\n\n"
                "⚡ **No teste você terá acesso a:**\n"
                "• Busca inteligente em 200+ domínios\n"
                "• Sistema de cache ultra-rápido\n"
                "• Histórico de buscas e favoritos\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "💻 By: Tequ1la"
            )
            
            member_buttons = visitor_buttons
        
        if is_callback:
            await event_or_user.edit(message, buttons=member_buttons)
        else:
            await respond_method(message, buttons=member_buttons)

@client.on(events.NewMessage(pattern=r'/start(.*)'))
async def start_command(event):
    try:
        referral_code = event.pattern_match.group(1).strip()
        await send_start_message(event, referral_code)
    except Exception as e:
        logger.error(f"Erro no start_command: {e}")
        try:
            await event.respond("❌ Ocorreu um erro. Tente novamente.")
        except:
            pass

@client.on(events.NewMessage(pattern=r'/resgatar (.*)'))
async def resgatar_command(event):
    token = event.pattern_match.group(1).strip()
    user_id = event.sender_id
    duration_days = validate_token(token)
    if duration_days is None:
        await event.respond('😕 𝗧𝗼𝗸𝗲𝗻 𝗶𝗻𝘃𝗮́𝗹𝗶𝗱𝗼 𝗼𝘂 𝗷𝗮́ 𝘂𝘁𝗶𝗹𝗶𝘇𝗮𝗱𝗼')
        return
    authorize_user_with_delta(user_id, timedelta(days=duration_days))
    use_token(token, user_id)
    process_conversion(user_id, token, duration_days)
    plan_name = f"{duration_days} dia(s)"
    if duration_days >= 36500: 
        plan_name = "Vitalício ✨"      
    await event.respond(f'🎉 𝗣𝗮𝗿𝗮𝗯𝗲́𝗻𝘀! Seu acesso de {plan_name} foi ativado com sucesso!')

@client.on(events.NewMessage(pattern=r'/afiliado'))
async def affiliate_command(event):
    # LIBERADO PARA TODOS OS USUÁRIOS
    user_id = event.sender_id
    me = await client.get_me()
    affiliate_link = f"https://t.me/{me.username}?start=ref{user_id}"
    stats = get_affiliate_stats(user_id)
    message = (
        f"💼 𝗦𝗲𝘂 𝗣𝗮𝗶𝗻𝗲𝗹 𝗱𝗲 𝗔𝗳𝗶𝗹𝗶𝗮𝗱𝗼\n\n"
        f"🔗 Seu Link de Convite:\n{affiliate_link}\n\n"
        f"📢 Compartilhe este link! Quando alguém iniciar o bot através dele e ativar um plano, você ganha {int(COMMISSION_RATE * 100)}% de comissão.\n\n"
        f"📊 𝗦𝘂𝗮𝘀 𝗘𝘀𝘁𝗮𝘁𝗶́𝘀𝘁𝗶𝗰𝗮𝘀:\n"
        f"  🔄 Conversões: {stats['conversions']}\n"
        f"  💰 Comissões Totais: R$ {stats['earnings']:.2f}\n"
        f"  💸 Disponível para Saque: R$ {stats['earnings']:.2f}\n\n"
    )
    buttons = [
        [Button.inline("💰 Solicitar Saque", b"withdraw_prompt"), Button.inline("🏆 Ver Top Afiliados", b"top_affiliates")],
        [Button.inline("⬅️ Voltar ao Menu", b"back_to_member_start")]
    ]
    await event.respond(message, buttons=buttons)

@client.on(events.NewMessage(pattern=r'/stats'))
async def stats_command(event):
    # LIBERADO PARA TODOS OS USUÁRIOS

    total_users = get_all_users_count()
    banned_users = get_banned_users_count()
    total_logins, total_domains = get_db_stats()

    stats_msg = (
        f"📊 𝗘𝘀𝘁𝗮𝘁𝗶́𝘀𝘁𝗶𝗰𝗮𝘀 𝗱𝗼 𝗕𝗼𝘁\n\n"
        f"👥 𝗨𝘀𝘂𝗮́𝗿𝗶𝗼𝘀:\n"
        f"• Total: {total_users:,}\n"
        f"• Banidos: {banned_users:,}\n"
        f"• Ativos: {total_users - banned_users:,}\n\n"
        f"🗄️ 𝗕𝗮𝗻𝗰𝗼 𝗱𝗲 𝗗𝗮𝗱𝗼𝘀:\n"
        f"• Total de Logins: {total_logins:,}\n"
        f"• Total de Domínios: {total_domains:,}\n\n"
        f"⚙️ 𝗦𝗶𝘀𝘁𝗲𝗺𝗮:\n"
        f"• Administradores: {len(ADMIN_IDS)}\n"
        f"• Status: ✅ Online"
    ).replace(",", ".")

    await event.respond(stats_msg)

@client.on(events.NewMessage(pattern=r'/top_afiliados'))
async def top_affiliates_command(event):
    message = "🏆 𝗥𝗮𝗻𝗸𝗶𝗻𝗴 𝗱𝗲 𝗔𝗳𝗶𝗹𝗶𝗮𝗱𝗼𝘀 - 𝗧𝗼𝗽 𝟭𝟬 🏆\n\n"
    with sqlite3.connect(DB_FILE) as conn:
        cur = conn.cursor()
        cur.execute("SELECT referrer_user_id, SUM(commission_amount) as total FROM commissions WHERE is_withdrawn = 0 GROUP BY referrer_user_id ORDER BY total DESC LIMIT 10")
        top_users = cur.fetchall()
    if not top_users:
        await event.respond("📊 Ainda não há dados suficientes para gerar um ranking.")
        return
    for i, (user_id, total) in enumerate(top_users):
        medal = "🥇" if i == 0 else "🥈" if i == 1 else "🥉" if i == 2 else f"{i+1}º"
        message += f"{medal} - ID: {user_id} - R$ {total:.2f}\n"
    await event.respond(message)

@client.on(events.NewMessage(pattern=r'/broadcast (.+)', outgoing=False))
async def broadcast_command(event):
    # LIBERADO PARA TODOS OS USUÁRIOS

    broadcast_message = event.pattern_match.group(1)

    # Confirmar o broadcast
    confirm_msg = (
        f"📢 **Confirmar Broadcast**\n\n"
        f"**Mensagem a ser enviada:**\n{broadcast_message}\n\n"
        f"⚠️ Esta mensagem será enviada para **todos os usuários** cadastrados.\n\n"
        f"Tem certeza?"
    )

    await event.respond(
        confirm_msg, 
        buttons=[
            [Button.inline("✅ Confirmar Envio", f"confirm_broadcast:{event.id}")],
            [Button.inline("❌ Cancelar", "cancel_broadcast")]
        ],
        parse_mode='Markdown'
    )

    # Armazenar a mensagem temporariamente
    global broadcast_temp_messages
    if 'broadcast_temp_messages' not in globals():
        broadcast_temp_messages = {}
    broadcast_temp_messages[event.id] = broadcast_message

async def send_broadcast_to_all(message_text: str, admin_id: int):
    """Enviar mensagem para todos os usuários cadastrados"""
    sent_count = 0
    failed_count = 0

    # Buscar todos os usuários
    with sqlite3.connect(DB_FILE) as conn:
        cur = conn.cursor()
        cur.execute("SELECT user_id, first_name FROM users")
        all_users = cur.fetchall()

    total_users = len(all_users)
    progress_msg = await client.send_message(
        admin_id, 
        f"📤 **Iniciando Broadcast**\n\n📊 Total de usuários: {total_users}\n✅ Enviados: 0\n❌ Falhas: 0"
    )

    for i, (user_id, first_name) in enumerate(all_users):
        try:
            # Personalizar mensagem com nome do usuário
            personalized_message = f"👋 Olá, {first_name}!\n\n📢 **Mensagem da Administração:**\n\n{message_text}\n\n🤖 @Olhosdecristo_bot"

            await client.send_message(user_id, personalized_message)
            sent_count += 1

            # Atualizar progresso a cada 10 envios ou no final
            if (i + 1) % 10 == 0 or (i + 1) == total_users:
                progress_text = (
                    f"📤 **Broadcast em Andamento**\n\n"
                    f"📊 Progresso: {i + 1}/{total_users} ({((i + 1)/total_users)*100:.1f}%)\n"
                    f"✅ Enviados: {sent_count}\n"
                    f"❌ Falhas: {failed_count}"
                )
                try:
                    await progress_msg.edit(progress_text)
                except:
                    pass

            # Pequeno delay para evitar flood
            await asyncio.sleep(0.1)

        except Exception as e:
            failed_count += 1
            logger.error(f"Erro ao enviar broadcast para {user_id}: {e}")

    # Mensagem final
    final_message = (
        f"✅ **Broadcast Concluído!**\n\n"
        f"📊 **Resultados:**\n"
        f"• Total de usuários: {total_users}\n"
        f"• Mensagens enviadas: {sent_count}\n"
        f"• Falhas: {failed_count}\n"
        f"• Taxa de sucesso: {(sent_count/total_users)*100:.1f}%"
    )

    await progress_msg.edit(final_message)
    await log_action(f"Broadcast enviado por admin {admin_id}: {sent_count} enviados, {failed_count} falhas")

@client.on(events.NewMessage(pattern=r'/dbinfo'))
async def db_info_command(event):
    # LIBERADO PARA TODOS OS USUÁRIOS

    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()

        # Verificar alguns domínios como exemplo
        cursor.execute("SELECT domain, COUNT(*) as count FROM logins GROUP BY domain ORDER BY count DESC LIMIT 10")
        top_domains = cursor.fetchall()

        # Verificar se há dados
        cursor.execute("SELECT COUNT(*) FROM logins")
        total_count = cursor.fetchone()[0]

        # Verificar alguns exemplos de dados
        cursor.execute("SELECT domain, login_data FROM logins LIMIT 5")
        sample_data = cursor.fetchall()

        # Verificar últimas inserções
        cursor.execute("SELECT domain, COUNT(*) FROM logins WHERE rowid > (SELECT MAX(rowid) - 1000 FROM logins) GROUP BY domain ORDER BY COUNT(*) DESC LIMIT 5")
        recent_additions = cursor.fetchall()

    info_msg = f"🗄️ **Informações Detalhadas do Banco**\n\n"
    info_msg += f"📊 **Total de Registros:** `{total_count:,}`\n\n"

    if top_domains:
        info_msg += f"🏆 **Top 10 Domínios:**\n"
        for domain, count in top_domains:
            info_msg += f"• `{domain}`: {count:,} logins\n"
    else:
        info_msg += "❌ **Nenhum domínio encontrado no banco!**\n"

    if recent_additions:
        info_msg += f"\n🆕 **Adições Recentes (últimas 1000 linhas):**\n"
        for domain, count in recent_additions:
            info_msg += f"• `{domain}`: {count:,} novos\n"

    if sample_data:
        info_msg += f"\n📝 **Exemplos de Dados:**\n"
        for domain, login_data in sample_data[:3]:
            # Ocultar dados sensíveis mostrando apenas formato
            masked_login = login_data[:20] + "..." if len(login_data) > 20 else login_data
            info_msg += f"• `{domain}`: {masked_login}\n"

    info_msg = info_msg.replace(",", ".")
    await event.respond(info_msg, parse_mode='Markdown')

@client.on(events.NewMessage(pattern=r'/add_login (.+)'))
async def add_manual_login(event):
    # LIBERADO PARA TODOS OS USUÁRIOS
    
    login_data = event.pattern_match.group(1).strip()
    
    # Validar formato email:senha
    if ':' not in login_data:
        await event.respond("❌ Formato inválido. Use: /add_login email:senha")
        return
    
    parts = login_data.split(':', 1)
    domain = extract_domain_final(parts[0])
    
    if not domain:
        await event.respond("❌ Não foi possível extrair o domínio do email.")
        return
    
    # Adicionar ao banco
    result = add_logins_to_db([(domain, login_data)])
    
    if result > 0:
        await event.respond(f"✅ Login adicionado com sucesso!\n\n📧 Email: {parts[0]}\n🌐 Domínio: {domain}")
    else:
        await event.respond("⚠️ Login já existe no banco ou erro ao inserir.")

@client.on(events.NewMessage(pattern=r'/add_cloud'))
async def add_cloud_command(event):
    """Comando para processar cloud no formato específico"""
    # LIBERADO PARA TODOS OS USUÁRIOS
    
    await event.respond(
        "📤 **Adicionar Cloud Formatada**\n\n"
        "✅ **Aceita ambos os formatos:**\n\n"
        "**1️⃣ Uma linha apenas:**\n"
        "```\n"
        "◉ URL: https://radar.serpro.gov.br/main.html\n"
        "◉ Login: 001.548.355-01\n"
        "◉ Senha: Anjo1980*\n"
        "```\n\n"
        "**2️⃣ Múltiplas linhas:**\n"
        "```\n"
        "🔎〕𝙍𝙀𝙎𝙐𝙇𝙏𝘼𝘿𝙊 1〔🔍〕\n"
        "◉ URL: https://radar.serpro.gov.br/main.html\n"
        "◉ Login: 001.548.355-01\n"
        "◉ Senha: Anjo1980*\n\n"
        "🔎〕𝙍𝙀𝙎𝙐𝙇𝙏𝘼𝘿𝙊 2〔🔍〕\n"
        "◉ URL: radar.serpro.gov.br\n"
        "◉ Login: 120.913.777-16\n"
        "◉ Senha: R123rr17*\n"
        "```\n\n"
        "📋 **Como usar:**\n"
        "• Cole seus dados após usar este comando\n"
        "• O bot detecta automaticamente o formato\n"
        "• Pode enviar 1 resultado ou vários de uma vez\n"
        "• O cabeçalho 🔎〕𝙍𝙀𝙎𝙐𝙇𝙏𝘼𝘿𝙊 é opcional\n\n"
        "🚀 **Processamento automático e inteligente!**\n\n"
        "🤖 @Olhosdecristo_bot",
        parse_mode='Markdown'
    )

def parse_cloud_data(text: str) -> list:
    """Extrai dados de login do formato cloud específico - versão ultra robusta"""
    import re
    
    processed_data = []
    
    # Dividir o texto em blocos de resultados
    result_blocks = re.split(r'〔🔎〕𝙍𝙀𝙎𝙐𝙇𝙏𝘼𝘿𝙊\s*\d+〔🔍〕', text, flags=re.IGNORECASE | re.MULTILINE)
    
    print(f"[CLOUD PARSER] Dividido em {len(result_blocks)} blocos")
    
    # Processar cada bloco individualmente
    for i, block in enumerate(result_blocks):
        if not block.strip():
            continue
            
        # Tentar extrair URL, Login e Senha de cada bloco
        url_match = re.search(r'◉\s*(?:URL|url):\s*([^\n\r◉]+)', block, re.IGNORECASE | re.MULTILINE)
        login_match = re.search(r'◉\s*(?:Login|login):\s*([^\n\r◉]+)', block, re.IGNORECASE | re.MULTILINE)
        senha_match = re.search(r'◉\s*(?:Senha|senha):\s*([^\n\r◉]+)', block, re.IGNORECASE | re.MULTILINE)
        
        if url_match and login_match and senha_match:
            url = url_match.group(1).strip()
            login = login_match.group(1).strip()
            senha = senha_match.group(1).strip()
            
            # Limpar dados mal formatados
            url = re.sub(r'\s+', ' ', url).strip()
            login = re.sub(r'\s+', ' ', login).strip()
            senha = re.sub(r'\s+', ' ', senha).strip()
            
            # Verificar se os dados são válidos
            if url and login and senha and senha.lower() not in ['undefined', 'null', '']:
                domain = extract_domain_from_url(url)
                if domain:
                    login_data = f"{login}:{senha}"
                    if (domain, login_data) not in processed_data:
                        processed_data.append((domain, login_data))
                        print(f"[CLOUD PARSER] Bloco {i}: {domain} - {login}")
    
    # Se ainda não capturou muitos, tentar método linha por linha mais agressivo
    if len(processed_data) < 60:  # Esperamos ~71 logins
        print(f"[CLOUD PARSER] Método de blocos capturou {len(processed_data)}, tentando linha por linha...")
        
        lines = text.split('\n')
        i = 0
        temp_data = []
        
        while i < len(lines):
            current_line = lines[i].strip()
            
            # Procurar por URL
            if '◉' in current_line and ('url' in current_line.lower() or 'URL' in current_line.lower()):
                url_match = re.search(r'◉\s*(?:URL|url):\s*(.+)', current_line, re.IGNORECASE)
                if url_match:
                    url = url_match.group(1).strip()
                    
                    # Procurar login nas próximas linhas
                    login = None
                    senha = None
                    
                    for j in range(i + 1, min(i + 10, len(lines))):  # Procurar nas próximas 10 linhas
                        line = lines[j].strip()
                        
                        if '◉' in line and ('login' in line.lower() or 'Login' in line.lower()) and not login:
                            login_match = re.search(r'◉\s*(?:Login|login):\s*(.+)', line, re.IGNORECASE)
                            if login_match:
                                login = login_match.group(1).strip()
                        
                        if '◉' in line and ('senha' in line.lower() or 'Senha' in line.lower()) and not senha:
                            senha_match = re.search(r'◉\s*(?:Senha|senha):\s*(.+)', line, re.IGNORECASE)
                            if senha_match:
                                senha = senha_match.group(1).strip()
                        
                        # Se encontrou ambos, adicionar
                        if login and senha:
                            # Limpar dados
                            url = re.sub(r'\s+', ' ', url).strip()
                            login = re.sub(r'\s+', ' ', login).strip()
                            senha = re.sub(r'\s+', ' ', senha).strip()
                            
                            if url and login and senha and senha.lower() not in ['undefined', 'null', '']:
                                domain = extract_domain_from_url(url)
                                if domain:
                                    login_data = f"{login}:{senha}"
                                    if (domain, login_data) not in processed_data and (domain, login_data) not in temp_data:
                                        temp_data.append((domain, login_data))
                                        print(f"[CLOUD PARSER] Linha {i}: {domain} - {login}")
                            break
            i += 1
        
        # Adicionar dados temporários aos processados
        processed_data.extend(temp_data)
    
    # Método final: regex global mais agressiva
    if len(processed_data) < 60:
        print(f"[CLOUD PARSER] Método linha por linha capturou {len(processed_data)}, tentando regex global...")
        
        # Padrão mais flexível que captura tudo
        pattern_global = r'◉\s*(?:URL|url):\s*([^\n\r◉]+)[\s\S]*?◉\s*(?:Login|login):\s*([^\n\r◉]+)[\s\S]*?◉\s*(?:Senha|senha):\s*([^\n\r◉]+)'
        
        matches_global = re.findall(pattern_global, text, re.IGNORECASE | re.MULTILINE | re.DOTALL)
        print(f"[CLOUD PARSER] Regex global encontrou: {len(matches_global)} matches")
        
        for url_raw, login_raw, senha_raw in matches_global:
            url = re.sub(r'\s+', ' ', url_raw.strip())
            login = re.sub(r'\s+', ' ', login_raw.strip())
            senha = re.sub(r'\s+', ' ', senha_raw.strip())
            
            if url and login and senha and senha.lower() not in ['undefined', 'null', '']:
                domain = extract_domain_from_url(url)
                if domain:
                    login_data = f"{login}:{senha}"
                    if (domain, login_data) not in processed_data:
                        processed_data.append((domain, login_data))
    
    print(f"[CLOUD PARSER] Total final processado: {len(processed_data)} logins")
    return processed_data

def extract_domain_from_url(url: str) -> str:
    """Extrai domínio de uma URL de forma robusta"""
    if not url:
        return ""
    
    # Limpar URL
    url = url.strip()
    
    # Verificar se já é apenas um domínio
    if not url.startswith(('http://', 'https://')):
        # Pode ser apenas domínio ou domínio/path
        domain = url.split('/')[0].lower()
        return domain if '.' in domain else ""
    
    # Extrair domínio da URL completa
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        return parsed.netloc.lower()
    except:
        # Fallback manual
        clean_url = url.replace('https://', '').replace('http://', '')
        domain = clean_url.split('/')[0].lower()
        return domain if '.' in domain else ""

@client.on(events.NewMessage(func=lambda e: not e.file and not e.message.message.startswith('/') and e.is_private and ('◉ URL:' in e.message.message and '◉ Login:' in e.message.message and '◉ Senha:' in e.message.message)))
async def process_cloud_data_handler(event):
    """Handler automático para processar dados cloud formatados - aceita 1 linha ou múltiplas"""
    try:
        sender = await event.get_sender()
        user_id = sender.id
        message_text = event.message.message
        
        # Verificar se contém o padrão básico necessário
        if '◉ URL:' in message_text and '◉ Login:' in message_text and '◉ Senha:' in message_text:
            
            msg = await event.respond("⚡ **PROCESSANDO CLOUD FORMATADA...**\n\n🔍 Extraindo dados automaticamente...")
            
            # Extrair dados
            processed_data = parse_cloud_data(message_text)
            
            if not processed_data:
                await msg.edit(
                    "❌ **Nenhum dado válido encontrado!**\n\n"
                    "💡 **Formato aceito:**\n"
                    "```\n"
                    "◉ URL: exemplo.com\n"
                    "◉ Login: usuario@email.com\n"
                    "◉ Senha: senha123\n"
                    "```\n\n"
                    "✅ **Pode enviar:**\n"
                    "• Apenas 1 resultado\n"
                    "• Múltiplos resultados de uma vez\n"
                    "• Com ou sem o cabeçalho 🔎〕𝙍𝙀𝙎𝙐𝙇𝙏𝘼𝘿𝙊\n\n"
                    "🔧 Verifique o formato e tente novamente.",
                    parse_mode='Markdown'
                )
                return
            
            await msg.edit(
                f"📊 **DADOS EXTRAÍDOS COM SUCESSO!**\n\n"
                f"🔢 Total de logins encontrados: {len(processed_data)}\n"
                f"⚡ Adicionando ao banco de dados..."
            )
            
            # Adicionar ao banco de dados
            try:
                added_count = add_logins_to_db(processed_data)
                
                # Contar domínios únicos
                unique_domains = set(domain for domain, _ in processed_data)
                
                # Logging detalhado para cada login adicionado
                await log_action(f"Cloud adicionada por usuário {user_id}: {added_count} logins de {len(unique_domains)} domínios únicos")
                
                success_message = (
                    f"✅ **CLOUD ADICIONADA COM SUCESSO!**\n\n"
                    f"📊 **Estatísticas:**\n"
                    f"• Logins processados: `{len(processed_data)}`\n"
                    f"• Logins adicionados: `{added_count}`\n"
                    f"• Duplicatas filtradas: `{len(processed_data) - added_count}`\n"
                    f"• Domínios únicos: `{len(unique_domains)}`\n\n"
                    f"🌐 **Domínios processados:**\n"
                )
                
                # Listar domínios únicos
                for domain in sorted(unique_domains):
                    domain_count = sum(1 for d, _ in processed_data if d == domain)
                    success_message += f"• `{domain}`: {domain_count} login(s)\n"
                
                success_message += f"\n🚀 **Dados disponíveis para busca!**\n🤖 @Olhosdecristo_bot"
                
                await msg.edit(success_message, parse_mode='Markdown')
                
                # Limpar cache para forçar nova busca nos domínios adicionados
                for domain in unique_domains:
                    # Remover do cache se existir
                    if hasattr(cache_inteligente, 'cache') and domain in cache_inteligente.cache:
                        del cache_inteligente.cache[domain]
                        print(f"[CACHE CLEAR] {domain} removido do cache para atualização")
                
            except Exception as db_error:
                await msg.edit(
                    f"❌ **Erro ao adicionar ao banco:**\n\n"
                    f"`{str(db_error)}`\n\n"
                    f"🔧 **Possíveis soluções:**\n"
                    f"• Verifique se o formato está correto\n"
                    f"• Tente novamente em alguns segundos\n"
                    f"• Contate o suporte se persistir"
                )
                logger.error(f"Erro ao processar cloud data: {db_error}")
        
    except Exception as e:
        logger.error(f"Erro no process_cloud_data_handler: {e}")
        try:
            await event.respond("❌ Erro interno ao processar dados. Tente novamente.")
        except:
            pass

@client.on(events.NewMessage(pattern=r'/bulk_add'))
async def bulk_add_prompt(event):
    # LIBERADO PARA TODOS OS USUÁRIOS
    
    await event.respond(
        "💾 **Adicionar Logins em Massa**\n\n"
        "**Métodos disponíveis:**\n\n"
        "1️⃣ **Upload de Arquivo** (Recomendado)\n"
        "   • Envie um arquivo .txt com logins\n"
        "   • Formato: email:senha (um por linha)\n"
        "   • Processamento automático e otimizado\n\n"
        "2️⃣ **Comando Manual**\n"
        "   • `/add_login email:senha`\n"
        "   • Para adições individuais\n\n"
        "3️⃣ **API Automática**\n"
        "   • Logins são salvos automaticamente durante buscas\n"
        "   • Sistema de cache inteligente ativo"
    )

@client.on(events.NewMessage(pattern=r'/check_db'))
async def check_db_command(event):
    # LIBERADO PARA TODOS OS USUÁRIOS

    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()

        # Verificar estrutura das tabelas
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = cursor.fetchall()

        # Verificar especificamente a tabela logins
        cursor.execute("PRAGMA table_info(logins);")
        login_schema = cursor.fetchall()

        # Contar registros por tabela
        table_counts = {}
        for table in tables:
            table_name = table[0]
            cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
            count = cursor.fetchone()[0]
            table_counts[table_name] = count

    check_msg = f"🔍 **Diagnóstico Completo do Banco**\n\n"
    check_msg += f"📋 **Tabelas Existentes:**\n"

    for table_name, count in table_counts.items():
        check_msg += f"• `{table_name}`: {count:,} registros\n"

    if login_schema:
        check_msg += f"\n🏗️ **Estrutura da Tabela Logins:**\n"
        for col_info in login_schema:
            check_msg += f"• `{col_info[1]}` ({col_info[2]})\n"

    check_msg = check_msg.replace(",", ".")
    await event.respond(check_msg, parse_mode='Markdown')

@client.on(events.NewMessage(pattern=r'/ping'))
async def ping_command(event):
    # LIBERADO PARA TODOS OS USUÁRIOS (mas informações extras para admins)

    sender = await event.get_sender()
    user_id = sender.id
    
    # Fazer ping básico
    start_time = time.time()
    msg = await event.respond("🏓 **Testando Ping...**")
    basic_latency = (time.time() - start_time) * 1000  # em ms
    
    if user_id in ADMIN_IDS:
        # Informações completas para administradores
        await msg.edit("🔍 **Realizando Diagnóstico Completo...**\n\n⚡ Testando conectividade...")
        
        ping_data = await check_bot_ping()
        
        if "error" in ping_data:
            await msg.edit(f"❌ **Erro no Ping:**\n\n`{ping_data['error']}`")
            return
        
        # Classificar latências
        def classify_latency(ms):
            if ms is None:
                return "❌ Falha"
            elif ms < 50:
                return f"🟢 Excelente ({ms:.1f}ms)"
            elif ms < 100:
                return f"🟡 Bom ({ms:.1f}ms)"
            elif ms < 200:
                return f"🟠 Regular ({ms:.1f}ms)"
            else:
                return f"🔴 Lento ({ms:.1f}ms)"
        
        ping_message = (
            f"🏓 **Diagnóstico Completo de Latência**\n\n"
            f"🤖 **Bot:** @{ping_data['bot_username']}\n"
            f"📅 **Timestamp:** {ping_data['timestamp'].strftime('%d/%m/%Y %H:%M:%S')}\n\n"
            f"📊 **Resultados de Latência:**\n\n"
            f"💬 **Telegram API:** {classify_latency(ping_data['telegram_latency'])}\n"
            f"💾 **Banco de Dados:** {classify_latency(ping_data['db_latency'])}\n"
            f"🧠 **Cache Sistema:** {classify_latency(ping_data['cache_latency'])}\n"
            f"🌐 **Conectividade Externa:** {classify_latency(ping_data['external_latency'])}\n"
            f"📡 **Status Externo:** {ping_data['external_status']}\n"
            f"⚡ **Resposta do Bot:** {classify_latency(basic_latency)}\n\n"
            f"🏆 **Performance Geral:**\n"
        )
        
        # Calcular performance geral
        latencies = [l for l in [ping_data['telegram_latency'], ping_data['db_latency'], ping_data['cache_latency']] if l is not None]
        if latencies:
            avg_latency = sum(latencies) / len(latencies)
            if avg_latency < 50:
                performance = "🚀 **EXCEPCIONAL**"
            elif avg_latency < 100:
                performance = "✅ **MUITO BOM**"
            elif avg_latency < 200:
                performance = "⚠️ **BOM**"
            else:
                performance = "🐌 **NECESSITA ATENÇÃO**"
            
            ping_message += f"{performance} (Média: {avg_latency:.1f}ms)\n\n"
        
        ping_message += "🔧 **Sistema:** Otimizado para dispositivos móveis potentes"
        
        buttons = [
            [Button.inline("🔄 Testar Novamente", b"refresh_ping"), Button.inline("📊 Estatísticas", b"stats")],
            [Button.inline("⬅️ Voltar ao Painel", b"back_to_admin")]
        ]
        
        await msg.edit(ping_message, buttons=buttons, parse_mode='Markdown')
    else:
        # Informações básicas para usuários comuns
        ping_message = (
            f"🏓 **Ping do Bot**\n\n"
            f"⚡ **Latência:** {classify_latency(basic_latency)}\n"
            f"🤖 **Status:** ✅ Online\n"
            f"📡 **Servidor:** Funcionando\n\n"
            f"💡 **Dica:** Use /start para acessar o menu principal"
        )
        
        def classify_latency(ms):
            if ms < 100:
                return f"🟢 {ms:.0f}ms"
            elif ms < 200:
                return f"🟡 {ms:.0f}ms"
            else:
                return f"🔴 {ms:.0f}ms"
        
        await msg.edit(ping_message)

@client.on(events.NewMessage(pattern=r'/cache'))
async def cache_stats_command(event):
    # LIBERADO PARA TODOS OS USUÁRIOS

    stats = cache_inteligente.get_stats()
    popular_domains = cache_inteligente.get_popular_domains(5)

    cache_msg = (
        f"🧠 **Estatísticas do Cache Inteligente**\n\n"
        f"📈 **Performance:**\n"
        f"• Total de Requests: `{stats['total_requests']:,}`\n"
        f"• Cache Hits: `{stats['cache_hits']:,}`\n"
        f"• Cache Misses: `{stats['cache_misses']:,}`\n"
        f"• Taxa de Acerto: `{stats['hit_rate']:.1f}%`\n\n"
        f"💾 **Armazenamento:**\n"
        f"• Domínios em Cache: `{stats['cached_domains']}`\n"
        f"• TTL: `12 horas`\n"
        f"• Limite Máximo: `150 domínios`\n\n"
    )

    if popular_domains:
        cache_msg += f"🔥 **Top 5 Domínios Mais Acessados:**\n"
        for domain, access_count in popular_domains:
            cache_msg += f"• `{domain}`: {access_count} acessos\n"

    cache_msg = cache_msg.replace(",", ".")

    buttons = [
        [Button.inline("🗑️ Limpar Cache", b"clear_cache"), Button.inline("📊 Atualizar Stats", b"refresh_cache_stats")],
        [Button.inline("⬅️ Voltar", b"back_to_admin")]
    ]

    await event.respond(cache_msg, parse_mode='Markdown', buttons=buttons)

@client.on(events.NewMessage(pattern=r'^/reset$'))
async def reset_handler(event):
    try:
        sender = await event.get_sender()
        id_user = sender.id
        hash_nome = str(id_user)

        tasks_canceladas.pop(hash_nome, None)
        usuarios_bloqueados.discard(id_user)
        usuarios_autorizados.pop(id_user, None)
        mensagens_origem.pop(id_user, None)
        urls_busca.pop(id_user, None)

        pasta_temp = os.path.join(TEMP_DIR, str(id_user))
        if os.path.exists(pasta_temp):
            shutil.rmtree(pasta_temp, ignore_errors=True)

        await event.reply(
            "✅ 𝗦𝗲𝘂𝘀 𝗱𝗮𝗱𝗼𝘀 𝗳𝗼𝗿𝗮𝗺 𝗿𝗲𝘀𝗲𝘁𝗮𝗱𝗼𝘀!\n\n🔄 Agora você pode utilizar os comandos novamente.\n⚡ Bot otimizado e mais leve!\n🚫 Buscas ativas foram canceladas.\n\n🤖 @Olhosdecristo_bot",
            buttons=[[Button.inline("❌ Apagar Mensagem", data=f"apagarmensagem:{id_user}")]]
        )

    except Exception as e:
        logger.error(f"Erro no reset_handler: {e}")
        await event.reply("❌ Erro ao resetar dados.")

@client.on(events.NewMessage(pattern=r'^/search$'))
async def search_no_params_handler(event):
    """Handler para /search sem parâmetros - mostrar instruções"""
    try:
        sender = await event.get_sender()
        id_user = sender.id
        
        instructions_text = (
            "🔍 **Como usar o comando de busca:**\n\n"
            "🧠 **Busca Inteligente (NOVO!):**\n"
            "`/search <termo>`\n\n"
            "✅ **Exemplos de busca inteligente:**\n"
            "• `/search netflix` - Detecta netflix.com\n"
            "• `/search google` - Detecta google.com\n"
            "• `/search facebook` - Detecta facebook.com\n"
            "• `/search .gov` - Busca todos os domínios .gov\n"
            "• `/search .edu` - Busca todos os domínios .edu\n"
            "• `/search youtube.com` - Busca direta\n\n"
            "🚀 **Funcionalidades:**\n"
            "• 🧠 Detecção automática de domínios\n"
            "• ⏸️ Pausa automática a cada 20k logins\n"
            "• 🔄 Opção de continuar ou parar\n"
            "• 🎯 Base de dados com 200+ domínios conhecidos\n"
            "• ⚡ Cache para resultados instantâneos\n\n"
            "💡 **Domínios suportados:**\n"
            "Redes sociais, streaming, governo, bancos, tech e muito mais!\n\n"
            "🤖 @Olhosdecristo_bot"
        )
        
        await event.respond(
            instructions_text,
            buttons=[[Button.inline("❌ Apagar Mensagem", data=f"apagarmensagem:{id_user}")]],
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Erro no search_no_params_handler: {e}")
        await event.respond("❌ Erro ao mostrar instruções. Tente: /search <dominio>")



async def continuar_busca_imediata(original_event, user_id, url, pause_count):
    """Continua busca imediatamente após pausa automática"""
    try:
        hash_nome = str(user_id)
        contador_atual = pause_count
        lock = asyncio.Lock()

        def contador_callback(novo_contador):
            nonlocal contador_atual
            contador_atual = novo_contador + pause_count  # Somar ao contador anterior

        # Tempo de início da continuação
        search_start_time = time.time()
        
        # Função para editar mensagem durante continuação
        async def editar_mensagem_continuacao():
            while not tasks_canceladas[hash_nome]['cancelled']:
                await asyncio.sleep(3)
                async with lock:
                    try:
                        # Calcular tempo decorrido
                        elapsed_seconds = time.time() - search_start_time
                        
                        # Formatar tempo decorrido
                        if elapsed_seconds < 60:
                            elapsed_str = f"{elapsed_seconds:.0f}s"
                        else:
                            minutes = int(elapsed_seconds // 60)
                            seconds = int(elapsed_seconds % 60)
                            elapsed_str = f"{minutes}m {seconds}s"
                        
                        # Calcular velocidade
                        new_logins = contador_atual - pause_count
                        if elapsed_seconds > 0 and new_logins > 0:
                            speed = new_logins / elapsed_seconds
                            speed_str = f"{speed:.0f}/s" if speed < 1000 else f"{speed/1000:.1f}k/s"
                        else:
                            speed_str = "Calculando..."
                        
                        # Barra de progresso
                        progress_dots = "⚡" * min(10, (new_logins // 100) % 10 + 1)
                        
                        new_text = f"🔄 𝗖𝗼𝗻𝘁𝗶𝗻𝘂𝗮𝗻𝗱𝗼 𝗕𝘂𝘀𝗰𝗮...\n\n{progress_dots}\n\n🎯 Total acumulado: {contador_atual:,}\n📊 Novos nesta sessão: {new_logins:,}\n⏱️ Tempo desta sessão: {elapsed_str}\n🚀 Velocidade: {speed_str}\n\n⚡ Buscando mais logins...\n\n🤖 @Olhosdecristo_bot".replace(",", ".")

                        await original_event.edit(
                            new_text,
                            buttons=[
                                [Button.inline("🚫 | PARAR PESQUISA", data=f"cancelarbusca:{user_id}")],
                                [Button.inline("❌ | APAGAR MENSAGEM", data=f"apagarmensagem:{user_id}")]
                            ]
                        )
                    except Exception as e:
                        if "not modified" not in str(e).lower():
                            logger.error(f"Erro ao editar mensagem de continuação: {e}")
                        pass

        # Iniciar task de edição
        tarefa_editar = asyncio.create_task(editar_mensagem_continuacao())

        # Buscar no banco local primeiro
        search_term = url.lower()
        pasta_temp = os.path.join(TEMP_DIR, str(user_id))
        
        # Buscar com LoginSearch (desabilitar pausa automática na continuação)
        search_instance = LoginSearch(url, user_id, pasta_temp, tasks_canceladas[hash_nome], contador_callback, limite_max=80000, search_term=search_term, disable_pause=True)
        arquivo_raw, arquivo_formatado = await asyncio.to_thread(search_instance.buscar)

        # Parar task de edição
        tarefa_editar.cancel()
        try:
            await tarefa_editar
        except asyncio.CancelledError:
            pass

        # Calcular tempo total
        total_time = time.time() - search_start_time
        time_str = f"{total_time:.1f}s" if total_time < 60 else f"{int(total_time // 60)}m {int(total_time % 60)}s"

        # Contar logins finais
        qtd_logins = contador_atual
        if os.path.exists(arquivo_raw):
            with open(arquivo_raw, "r", encoding="utf-8") as f:
                qtd_logins = sum(1 for _ in f)

        # Verificar se houve nova pausa automática
        if 'pause_at' in tasks_canceladas[hash_nome]:
            new_pause_count = tasks_canceladas[hash_nome]['pause_at']
            await original_event.edit(
                f"⏸️ 𝗡𝗼𝘃𝗮 𝗣𝗮𝘂𝘀𝗮 𝗔𝘂𝘁𝗼𝗺𝗮́𝘁𝗶𝗰𝗮 𝗮𝗼𝘀 𝟮𝟬𝗞!\n\n"
                f"🎯 {new_pause_count:,} logins encontrados até agora\n"
                f"📊 Novos nesta sessão: {new_pause_count - pause_count:,}\n"
                f"⏱️ Tempo desta sessão: {time_str}\n\n"
                f"📋 O que deseja fazer?\n\n"
                f"🔄 **Continuar Busca** - Buscar mais 20k logins\n"
                f"🛑 **Parar Aqui** - Finalizar com {new_pause_count:,} logins\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"💻 By: Tequ1la".replace(",", "."),
                buttons=[
                    [Button.inline("🔄 Continuar Busca", data=f"continue_search:{user_id}"),
                     Button.inline(f"🛑 Parar com {new_pause_count:,}".replace(",", "."), data=f"stop_at_pause:{user_id}")],
                    [Button.inline("❌ Cancelar Tudo", data=f"cancel:{user_id}")]
                ]
            )
            usuarios_bloqueados.discard(user_id)
            return

        # Busca finalizada - mostrar resultado
        await original_event.edit(
            f"✅ 𝗖𝗼𝗻𝘁𝗶𝗻𝘂𝗮𝗰̧𝗮̃𝗼 𝗖𝗼𝗻𝗰𝗹𝘂í𝗱𝗮!\n\n"
            f"🎯 Total final: {qtd_logins:,}\n"
            f"📊 Novos nesta sessão: {qtd_logins - pause_count:,}\n"
            f"⏱️ Tempo desta sessão: {time_str}\n\n"
            f"📋 Escolha o formato de download:\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💻 By: Tequ1la".replace(",", "."),
            buttons=[
                [Button.inline("📝 USER:PASS", data=f"format1:{user_id}"),
                 Button.inline("📋 FORMATADO", data=f"format2:{user_id}")],
                [Button.inline("❌ CANCELAR", data=f"cancel:{user_id}")]
            ]
        )

        usuarios_bloqueados.discard(user_id)
        
    except Exception as e:
        logger.error(f"Erro ao continuar busca: {e}")
        await original_event.edit("❌ Erro ao continuar busca. Use /reset e tente novamente.")
        usuarios_bloqueados.discard(user_id)

async def simular_continuacao_busca(original_event, user_id, url):
    """Simula continuação da busca sem duplicar código"""
    try:
        # Simular novo evento de busca
        from types import SimpleNamespace
        fake_event = SimpleNamespace()
        fake_event.pattern_match = SimpleNamespace()
        fake_event.pattern_match.group = lambda x: url
        fake_event.sender_id = user_id
        fake_event.get_sender = original_event.get_sender
        fake_event.respond = original_event.respond
        fake_event.reply = original_event.edit
        fake_event.chat_id = original_event.chat_id
        fake_event.id = original_event.id
        
        # Chamar handler de busca
        await search_handler(fake_event)
    except Exception as e:
        logger.error(f"Erro ao continuar busca: {e}")
        await original_event.edit("❌ Erro ao continuar busca. Use /reset e tente novamente.")

@client.on(events.NewMessage(pattern=r'/favoritos'))
async def favoritos_command(event):
    """Gerenciar domínios favoritos"""
    try:
        sender = await event.get_sender()
        user_id = sender.id
        
        favorites = get_user_favorites(user_id)
        
        if not favorites:
            message = (
                "⭐ **Seus Domínios Favoritos**\n\n"
                "📭 Nenhum domínio favorito ainda.\n\n"
                "💡 **Como adicionar favoritos:**\n"
                "• Após uma busca bem-sucedida, use o botão ⭐\n"
                "• Ou use: `/add_fav <dominio>`\n\n"
                "🚀 **Benefícios dos favoritos:**\n"
                "• Acesso rápido aos seus domínios preferidos\n"
                "• Estatísticas detalhadas\n"
                "• Busca com um clique\n\n"
                "🤖 @Olhosdecristo_bot"
            )
            buttons = [[Button.inline("❌ Fechar", data=f"apagarmensagem:{user_id}")]]
        else:
            message = "⭐ **Seus Domínios Favoritos**\n\n"
            
            buttons = []
            for i, (domain, added_at) in enumerate(favorites[:10]):  # Limitar a 10 favoritos
                try:
                    added_date = datetime.fromisoformat(added_at).strftime("%d/%m/%Y")
                except:
                    added_date = "Data inválida"
                
                message += f"🔸 **{domain}**\n"
                message += f"   📅 Adicionado: {added_date}\n\n"
                
                # Criar botões em pares
                if i % 2 == 0:
                    if i + 1 < len(favorites):
                        next_domain = favorites[i + 1][0]
                        buttons.append([
                            Button.inline(f"🔍 {domain[:15]}...", data=f"search_fav:{domain}"),
                            Button.inline(f"🔍 {next_domain[:15]}...", data=f"search_fav:{next_domain}")
                        ])
                    else:
                        buttons.append([Button.inline(f"🔍 {domain[:20]}...", data=f"search_fav:{domain}")])
            
            if len(favorites) > 10:
                message += f"... e mais {len(favorites) - 10} domínios\n\n"
            
            message += "🔍 **Clique em um domínio para buscar rapidamente!**"
            buttons.append([Button.inline("❌ Fechar", data=f"apagarmensagem:{user_id}")])
        
        await event.respond(message, buttons=buttons, parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"Erro no favoritos_command: {e}")
        await event.respond("❌ Erro ao carregar favoritos. Tente novamente.")

@client.on(events.NewMessage(pattern=r'/add_fav (.+)'))
async def add_favorite_command(event):
    """Adicionar domínio aos favoritos"""
    try:
        domain = event.pattern_match.group(1).strip()
        sender = await event.get_sender()
        user_id = sender.id
        
        # Verificar se é um domínio válido
        domain_detected = detectar_dominio_inteligente(domain)
        if not domain_detected:
            await event.respond("❌ Domínio inválido. Tente com um domínio válido.")
            return
        
        add_favorite_domain(user_id, domain_detected)
        
        await event.respond(
            f"⭐ **Domínio adicionado aos favoritos!**\n\n"
            f"🌐 **Domínio:** `{domain_detected}`\n"
            f"📅 **Adicionado em:** {datetime.now(SAO_PAULO_TZ).strftime('%d/%m/%Y %H:%M')}\n\n"
            f"💡 Use `/favoritos` para ver todos os seus favoritos.",
            buttons=[[Button.inline("⭐ Ver Favoritos", data="show_favorites"), Button.inline("❌ Fechar", data=f"apagarmensagem:{user_id}")]],
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Erro no add_favorite_command: {e}")
        await event.respond("❌ Erro ao adicionar favorito. Tente novamente.")

@client.on(events.NewMessage(pattern=r'/remove_fav (.+)'))
async def remove_favorite_command(event):
    """Remover domínio dos favoritos"""
    try:
        domain = event.pattern_match.group(1).strip()
        sender = await event.get_sender()
        user_id = sender.id
        
        remove_favorite_domain(user_id, domain)
        
        await event.respond(
            f"🗑️ **Domínio removido dos favoritos!**\n\n"
            f"🌐 **Domínio:** `{domain}`\n\n"
            f"💡 Use `/favoritos` para ver seus favoritos restantes.",
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Erro no remove_favorite_command: {e}")
        await event.respond("❌ Erro ao remover favorito. Tente novamente.")

@client.on(events.NewMessage(pattern=r'/teste'))
async def teste_command(event):
    """Ativar período de teste de 30 minutos"""
    try:
        sender = await event.get_sender()
        user_id = sender.id
        
        # Verificar se é admin ou já tem acesso
        if user_id in ADMIN_IDS:
            await event.respond("👑 **Você já é administrador!**\n\nTem acesso total a todas as funcionalidades.")
            return
        
        if is_authorized(user_id):
            await event.respond("✅ **Você já tem acesso premium!**\n\nTodas as funcionalidades estão liberadas.")
            return
        
        # Tentar iniciar teste
        if start_trial(user_id):
            await event.respond(
                "🎉 **TESTE GRATUITO ATIVADO!**\n\n"
                "✅ **Parabéns!** Você ganhou 30 minutos de acesso completo!\n\n"
                "🚀 **Agora você pode:**\n"
                "• 🔍 Fazer buscas ilimitadas\n"
                "• ⭐ Usar sistema de favoritos\n"
                "• 📜 Acessar histórico de buscas\n"
                "• 💼 Usar painel de afiliado\n"
                "• 🧠 Aproveitar cache inteligente\n\n"
                "⏰ **Tempo restante:** 30 minutos\n\n"
                "💡 **Dica:** Use `/start` para acessar o menu completo!\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "💻 By: Tequ1la",
                buttons=[
                    [Button.inline("🚀 Acessar Menu Completo", b"back_to_start")],
                    [Button.inline("🔍 Fazer Primeira Busca", b"prompt_search")],
                    [Button.inline("💎 Ver Planos Premium", b"group_plans")]
                ]
            )
            await log_action(f"Usuário {user_id} ({sender.first_name}) ativou teste gratuito de 30 minutos")
        else:
            trial_status = get_trial_status(user_id)
            if trial_status["trial_used"]:
                await event.respond(
                    "🚫 **Teste já utilizado!**\n\n"
                    "Você já usou seu período de teste gratuito de 30 minutos.\n\n"
                    "💎 **Para continuar usando:**\n"
                    "• Adquira um plano premium\n"
                    "• Use um token se possuir\n\n"
                    "💬 **Contate o suporte para mais informações:**\n"
                    "@Tequ1ladoxxado\n\n"
                    "🤖 @Olhosdecristo_bot",
                    buttons=[
                        [Button.inline("💎 Ver Planos", b"group_plans")],
                        [Button.inline("🔑 Resgatar Token", b"redeem_token_prompt")],
                        [Button.url("💬 Suporte", "https://t.me/Tequ1ladoxxado")]
                    ]
                )
            else:
                await event.respond(
                    "⚠️ **Erro ao ativar teste**\n\n"
                    "Não foi possível ativar o período de teste. Tente novamente ou contate o suporte.\n\n"
                    "🤖 @Olhosdecristo_bot"
                )
        
    except Exception as e:
        logger.error(f"Erro no teste_command: {e}")
        await event.respond("❌ Erro interno. Tente novamente mais tarde.")

@client.on(events.NewMessage(pattern=r'/comandos'))
async def comandos_handler(event):
    """Handler para mostrar lista de comandos disponíveis"""
    try:
        sender = await event.get_sender()
        user_id = sender.id
        
        if user_id in ADMIN_IDS:
            # Comandos para administradores
            comandos_text = (
                "📋 **Lista de Comandos - Administrador**\n\n"
                "**👑 Comandos Básicos:**\n"
                "• `/start` - Menu principal de administração\n"
                "• `/search <termo>` - Buscar logins inteligente\n"
                "• `/reset` - Resetar dados e cancelar buscas\n"
                "• `/comandos` - Mostrar esta lista\n\n"
                "**📊 Comandos de Sistema:**\n"
                "• `/stats` - Estatísticas completas do bot\n"
                "• `/ping` - Teste de latência e conectividade\n"
                "• `/cache` - Informações do cache inteligente\n"
                "• `/dbinfo` - Informações detalhadas do banco\n"
                "• `/check_db` - Verificar estrutura do banco\n\n"
                "**👥 Gerenciamento de Usuários:**\n"
                "• `/ban <ID>` - Banir usuário\n"
                "• `/unban <ID>` - Desbanir usuário\n"
                "• `/autorizar <ID> <tempo>` - Autorizar usuário (7d, 30d)\n"
                "• `/cancelar <ID>` - Cancelar plano do usuário\n"
                "• `/info <ID>` - Informações detalhadas do usuário\n"
                "• `/reload_admins` - Recarregar lista de admins\n\n"
                "**📢 Comunicação:**\n"
                "• `/broadcast <mensagem>` - Enviar para todos usuários\n"
                "• `/top_afiliados` - Ranking de afiliados\n\n"
                "**💾 Gerenciamento de Dados:**\n"
                "• `/add_login <email:senha>` - Adicionar login manual\n"
                "• `/bulk_add` - Instruções para adicionar em massa\n"
                "• `/add_cloud` - Processar cloud formatada\n"
                "• Enviar arquivo .txt - Upload automático de logins\n\n"
                "**💰 Sistema de Afiliados:**\n"
                "• `/afiliado` - Painel de afiliado (todos usuários)\n"
                "• Gerar tokens pelo painel admin (/start)\n\n"
                "**🔍 Busca Inteligente Avançada:**\n"
                "• `/search netflix` - Detecta netflix.com\n"
                "• `/search sisregiii` - Detecta sisregiii.saude.gov.br\n"
                "• `/search .gov` - Busca todos domínios .gov\n"
                "• `/search .edu` - Busca todos domínios .edu\n"
                "• `/search google.com` - Busca direta por domínio\n"
                "• Sistema detecta 200+ domínios automaticamente\n\n"
                "**⭐ Recursos Premium:**\n"
                "• `/favoritos` - Gerenciar domínios favoritos\n"
                "• `/add_fav <dominio>` - Adicionar favorito\n"
                "• `/remove_fav <dominio>` - Remover favorito\n"
                "• Sistema de histórico de buscas\n"
                "• Export em JSON e formatos personalizados\n\n"
                "**🎮 Funcionalidades Especiais:**\n"
                "• Upload de arquivos até 1GB (TURBO MODE)\n"
                "• Cache inteligente com 24h TTL\n"
                "• Busca combinada (API + Banco local)\n"
                "• Pausa automática a cada 20k logins\n"
                "• Sistema de continuação de busca\n"
                "• Logs automáticos de todas as ações\n\n"
                "**🔧 Painel Admin (Botões):**\n"
                "• Gerar Tokens (1d, 7d, 30d, 60d, 90d, Vitalício)\n"
                "• Broadcast com confirmação\n"
                "• Export completo de usuários\n"
                "• Limpar banco de dados\n"
                "• Estatísticas em tempo real\n"
                "• Auditoria e logs\n\n"
                "🤖 @Olhosdecristo_bot\n"
                "💻 **Otimizado para S24 Ultra e dispositivos potentes**"
            )
        elif is_authorized(user_id):
            # Comandos para usuários autorizados
            comandos_text = (
                "📋 **Lista de Comandos - Usuário Premium**\n\n"
                "**🔍 Comandos de Busca:**\n"
                "• `/start` - Menu principal\n"
                "• `/search <termo>` - Buscar logins\n"
                "• `/reset` - Cancelar busca ativa e deixar o bot mais leve\n"
                "• `/ping` - Teste de latência do bot\n"
                "• `/afiliado` - Painel de afiliado\n\n"
                "**⭐ Comandos de Favoritos:**\n"
                "• `/favoritos` - Ver domínios favoritos\n"
                "• `/add_fav <dominio>` - Adicionar favorito\n"
                "• `/remove_fav <dominio>` - Remover favorito\n\n"
                "**📤 Comandos de Cloud:**\n"
                "• `/add_cloud` - Processar cloud formatada\n"
                "• `/add_login <email:senha>` - Adicionar login manual\n\n"
                "**🧠 Busca Inteligente:**\n"
                "• `/search netflix` - Detecta netflix.com\n"
                "• `/search google` - Detecta google.com\n"
                "• `/search .gov` - Busca domínios .gov\n"
                "• `/search facebook` - Detecta facebook.com\n"
                "• `/search youtube.com` - Busca direta\n\n"
                "**💡 Exemplos de Domínios Suportados:**\n"
                "• Redes sociais (facebook, instagram, twitter)\n"
                "• Streaming (netflix, youtube, spotify)\n"
                "• Tecnologia (google, microsoft, apple)\n"
                "• Governo (.gov, .edu, receita)\n"
                "• Bancos (nubank, itau, santander)\n\n"
                "**🎯 Funcionalidades:**\n"
                "• ⚡ Cache inteligente para resultados rápidos\n"
                "• 🔄 Pausa automática a cada 20k logins\n"
                "• 📊 Histórico de buscas\n"
                "• 📱 Otimizado para dispositivos móveis\n\n"
                "🤖 @Olhosdecristo_bot"
            )
        else:
            # Comandos para usuários não autorizados
            comandos_text = (
                "📋 **Lista de Comandos - Visitante**\n\n"
                "**🚀 Comandos Disponíveis:**\n"
                "• `/start` - Menu principal\n"
                "• `/ping` - Teste de latência básico\n"
                "• `/reset` - Cancelar busca e otimizar o bot\n"
                "• `/resgatar <token>` - Resgatar token\n"
                "• `/teste` - Ativar 30 minutos de teste GRÁTIS\n\n"
                "**🆓 Teste Gratuito:**\n"
                "• 30 minutos de acesso completo\n"
                "• Todas as funcionalidades liberadas\n"
                "• Teste apenas 1 vez por usuário\n\n"
                "**💎 Para Acessar Todas as Funcionalidades:**\n"
                "• Adquira um plano premium\n"
                "• Use `/start` para ver opções\n"
                "• Contate o suporte para mais informações\n\n"
                "**🔍 Recursos Premium:**\n"
                "• Busca inteligente de logins\n"
                "• Cache para resultados instantâneos\n"
                "• Suporte a 200+ domínios conhecidos\n"
                "• Histórico de buscas\n"
                "• Sistema de afiliados\n\n"
                "🤖 @Olhosdecristo_bot"
            )
        
        await event.respond(
            comandos_text,
            buttons=[[Button.inline("❌ Apagar Mensagem", data=f"apagarmensagem:{user_id}")]],
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Erro no comandos_handler: {e}")
        await event.respond("❌ Erro ao mostrar comandos. Tente novamente.")

@client.on(events.NewMessage(pattern=r'^/search (.+)$'))
async def search_handler(event):
    try:
        termo_completo = event.pattern_match.group(1).strip()
        sender = await event.get_sender()
        id_user = sender.id
        
        # Verificar se tem acesso (autorizado, admin ou teste)
        has_user_access, access_type = has_access(id_user)
        
        if not has_user_access:
            await event.reply(
                "🚫 **Acesso Negado**\n\n"
                "Para usar o sistema de busca, você precisa de:\n\n"
                "🆓 **Teste gratuito** - Use `/teste` para 30 min\n"
                "💎 **Plano premium** - Acesso ilimitado\n"
                "🔑 **Token** - Use `/resgatar <token>`\n\n"
                "🤖 @Olhosdecristo_bot",
                buttons=[
                    [Button.inline("🆓 Ativar Teste", b"start_trial")],
                    [Button.inline("💎 Ver Planos", b"group_plans")],
                    [Button.inline("🔑 Resgatar Token", b"redeem_token_prompt")]
                ]
            )
            return

        # SISTEMA INTELIGENTE: Identificar domínio automaticamente
        termo_original = termo_completo
        url_final = detectar_dominio_inteligente(termo_completo)
        
        if not url_final:
            return await event.reply(
                "❌ 𝗡𝗮̃𝗼 𝗳𝗼𝗶 𝗽𝗼𝘀𝘀í𝘃𝗲𝗹 𝗶𝗱𝗲𝗻𝘁𝗶𝗳𝗶𝗰𝗮𝗿 𝗼 𝗱𝗼𝗺í𝗻𝗶𝗼\n\n💡 Exemplos:\n• /search netflix\n• /search google.com\n• /search .gov\n\n🤖 @Olhosdecristo_bot",
                buttons=[[Button.inline("❌ Apagar Mensagem", data=f"apagarmensagem:{id_user}")]]
            )

        # Verificar se o usuário já tem uma busca em progresso
        if id_user in usuarios_bloqueados:
            hash_nome = str(id_user)
            busca_atual = urls_busca.get(id_user, "desconhecida")
            
            # Verificar se a busca ainda está ativa
            if hash_nome in tasks_canceladas and not tasks_canceladas[hash_nome].get('cancelled', False):
                return await event.reply(
                    f"⚠️ 𝗩𝗼𝗰𝗲̂ 𝗷𝗮́ 𝘁𝗲𝗺 𝘂𝗺𝗮 𝗯𝘂𝘀𝗰𝗮 𝗲𝗺 𝗮𝗻𝗱𝗮𝗺𝗲𝗻𝘁𝗼!\n\n"
                    f"🔍 Busca atual: {busca_atual}\n\n"
                    f"📋 **Opções disponíveis:**\n\n"
                    f"🔴 **Cancelar busca atual** - Use `/reset`\n"
                    f"⏳ **Aguardar conclusão** - Espere a busca terminar\n\n"
                    f"⚡ **Dica:** Você pode acompanhar o progresso da busca atual ou cancelá-la para iniciar uma nova.\n\n"
                    f"🤖 @Olhosdecristo_bot",
                    buttons=[
                        [Button.inline("🔴 Cancelar Busca Atual", data=f"cancelarbusca:{id_user}")],
                        [Button.inline("📊 Ver Progresso", data=f"ver_progresso:{id_user}")],
                        [Button.inline("❌ Apagar Mensagem", data=f"apagarmensagem:{id_user}")]
                    ]
                )
            else:
                # Se não tem busca ativa, remover do bloqueio
                usuarios_bloqueados.discard(id_user)

        url = url_final
        usuarios_bloqueados.add(id_user)

        nome = f"{getattr(sender, 'first_name', '')} {getattr(sender, 'last_name', '')}".strip()
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        hash_nome = str(id_user)

        usuarios_autorizados[id_user] = hash_nome
        mensagens_origem[id_user] = safe_telegram_id(event.id)
        urls_busca[id_user] = url
        tasks_canceladas[hash_nome] = {'cancelled': False}

        pasta_temp = os.path.join(TEMP_DIR, str(id_user))
        os.makedirs(pasta_temp, exist_ok=True)

        # Preparar nome do arquivo baseado na URL
        url_clean = re.sub(r'[^\w\-_\.]', '_', url)
        if url_clean.startswith('_'):
            url_clean = url_clean[1:]
        if url_clean.endswith('_'):
            url_clean = url_clean[:-1]

        # Verificar se está no cache antes de mostrar mensagem de busca
        cached_check = cache_inteligente.get(url)
        
        # Texto base com informações da busca
        search_info = f"🔍 Termo buscado: '{termo_original}'\n🌐 Domínio identificado: {url}"
        if termo_original.lower() != url.lower():
            search_info += f"\n🧠 Detecção automática de domínio ativada"
        
        if cached_check is not None:
            initial_text = f"⚡ 𝗖𝗮𝗰𝗵𝗲 𝗛𝗶𝘁! 𝗥𝗲𝘀𝘂𝗹𝘁𝗮𝗱𝗼 𝗶𝗻𝘀𝘁𝗮𝗻𝘁𝗮̂𝗻𝗲𝗼...\n\n{search_info}\n🔍 Logins encontrados: {len(cached_check):,}\n\n✨ Dados do cache inteligente\n\n🤖 @Olhosdecristo_bot".replace(",", ".")
        else:
            initial_text = f"☁️ 𝗣𝗿𝗼𝗰𝘂𝗿𝗮𝗻𝗱𝗼 𝗱𝗮𝗱𝗼𝘀 𝗱𝗮 𝗨𝗥𝗟 𝗳𝗼𝗿𝗻𝗲𝗰𝗶𝗱𝗮...\n\n{search_info}\n🔍 Logins encontrados: 0\n\n⚡ Sistema inteligente ativo\n\n🤖 @Olhosdecristo_bot"

        # Corrigir overflow de inteiros limitando valores
        safe_event_id = None
        try:
            if hasattr(event, 'id') and event.id:
                # Garantir que o ID está dentro dos limites seguros
                if -2147483648 <= event.id <= 2147483647:
                    safe_event_id = event.id
        except:
            pass

        msg_busca = await client.send_message(
            event.chat_id,
            initial_text,
            buttons=[
                [Button.inline("🚫 Parar Pesquisa", data=f"cancelarbusca:{id_user}")],
                [Button.inline("❌ Apagar Mensagem", data=f"apagarmensagem:{id_user}")]
            ],
            reply_to=safe_event_id
        )

        contador_atual = 0
        lock = asyncio.Lock()

        def contador_callback(novo_contador):
            nonlocal contador_atual
            contador_atual = novo_contador

        # Tempo de início da busca
        search_start_time = time.time()
        
        async def editar_mensagem_periodicamente():
            while not tasks_canceladas[hash_nome]['cancelled']:
                await asyncio.sleep(3)  # Atualizar a cada 3 segundos para melhor UX
                async with lock:
                    try:
                        # Calcular tempo decorrido
                        current_time = time.time()
                        elapsed_seconds = current_time - search_start_time
                        
                        # Formatar tempo decorrido
                        if elapsed_seconds < 60:
                            elapsed_str = f"{elapsed_seconds:.0f}s"
                        else:
                            minutes = int(elapsed_seconds // 60)
                            seconds = int(elapsed_seconds % 60)
                            elapsed_str = f"{minutes}m {seconds}s"
                        
                        # Calcular velocidade atual
                        if elapsed_seconds > 0 and contador_atual > 0:
                            speed = contador_atual / elapsed_seconds
                            if speed > 1000:
                                speed_str = f"{speed/1000:.1f}k/s"
                            else:
                                speed_str = f"{speed:.0f}/s"
                        else:
                            speed_str = "Calculando..."
                        
                        # Estimar tempo restante (se houver dados suficientes)
                        if speed > 0 and contador_atual > 50:
                            # Estimar baseado na velocidade atual
                            estimated_total = min(contador_atual * 2, 80000)  # Estimativa conservadora
                            remaining = estimated_total - contador_atual
                            eta_seconds = remaining / speed
                            
                            if eta_seconds < 60:
                                eta_str = f"{eta_seconds:.0f}s"
                            else:
                                eta_minutes = int(eta_seconds // 60)
                                eta_secs = int(eta_seconds % 60)
                                eta_str = f"{eta_minutes}m {eta_secs}s"
                        else:
                            eta_str = "Calculando..."
                        
                        # Verificar se é cache hit
                        if cached_check is not None:
                            new_text = f"⚡ 𝗖𝗮𝗰𝗵𝗲 𝗛𝗶𝘁! 𝗥𝗲𝘀𝘂𝗹𝘁𝗮𝗱𝗼 𝗶𝗻𝘀𝘁𝗮𝗻𝘁𝗮̂𝗻𝗲𝗼...\n\n✨✨✨✨✨✨✨✨✨✨\n\n🔍 Logins encontrados: {contador_atual:,}\n⏱️ Tempo decorrido: {elapsed_str}\n\n⚡ Cache inteligente ativo!\n\n🤖 @Olhosdecristo_bot".replace(",", ".")
                        else:
                            # Criar uma barra de progresso visual
                            if contador_atual > 0:
                                # Barra de progresso baseada na velocidade
                                progress_dots = "⚡" * min(10, (contador_atual // 100) % 10 + 1)
                                new_text = f"☁️ 𝗣𝗿𝗼𝗰𝘂𝗿𝗮𝗻𝗱𝗼 𝗱𝗮𝗱𝗼𝘀 𝗱𝗮 𝗨𝗥𝗟 𝗳𝗼𝗿𝗻𝗲𝗰𝗶𝗱𝗮...\n\n{progress_dots}\n\n🔍 Logins encontrados: {contador_atual:,}\n⏱️ Tempo decorrido: {elapsed_str}\n🚀 Velocidade: {speed_str}\n⏳ Tempo restante: ~{eta_str}\n\n⚡ Buscando em tempo real...\n\n🤖 @Olhosdecristo_bot".replace(",", ".")
                            else:
                                new_text = f"☁️ 𝗣𝗿𝗼𝗰𝘂𝗿𝗮𝗻𝗱𝗼 𝗱𝗮𝗱𝗼𝘀 𝗱𝗮 𝗨𝗥𝗟 𝗳𝗼𝗿𝗻𝗲𝗰𝗶𝗱𝗮...\n\n⏳ Iniciando busca...\n\n🔍 Logins encontrados: {contador_atual}\n⏱️ Tempo decorrido: {elapsed_str}\n\n⚡ Preparando busca...\n\n🤖 @Olhosdecristo_bot"

                        await msg_busca.edit(
                            new_text,
                            buttons=[
                                [Button.inline("🚫 | PARAR PESQUISA", data=f"cancelarbusca:{id_user}")],
                                [Button.inline("❌ | APAGAR MENSAGEM", data=f"apagarmensagem:{id_user}")]
                            ]
                        )
                        print(f"[SEARCH PROGRESS] {contador_atual} logins encontrados para {url} em {elapsed_str}")
                    except Exception as e:
                        if "not modified" not in str(e).lower() and "message not found" not in str(e).lower():
                            logger.error(f"Erro ao editar mensagem: {e}")
                        pass

        tarefa_editar = asyncio.create_task(editar_mensagem_periodicamente())

        def buscar_wrapper():
            try:
                # Verificar cache primeiro
                cached_results = cache_inteligente.get(url)

                if cached_results is not None:
                    # Cache HIT! Usar resultados já combinados do cache
                    print(f"[CACHE HIT] {url} - Usando resultados combinados do cache")

                    arquivo_raw = os.path.join(pasta_temp, f"{id_user}.txt")
                    arquivo_formatado = os.path.join(pasta_temp, f"{id_user}_formatado.txt")

                    # Atualizar callback com total do cache
                    contador_callback(len(cached_results))

                    # Criar arquivo raw com nome baseado na URL
                    arquivo_raw = os.path.join(pasta_temp, f"{url_clean}_logins.txt")
                    arquivo_formatado = os.path.join(pasta_temp, f"{url_clean}_formatado.txt")

                    # Criar arquivo raw
                    with open(arquivo_raw, 'w', encoding='utf-8') as f:
                        f.write(f"# =====================================\n")
                        f.write(f"# 🤖 Bot: Olhos de Cristo Bot\n")
                        f.write(f"# 📱 Telegram: @Olhosdecristo_bot\n")
                        f.write(f"# 🌐 Domínio: {url}\n")
                        f.write(f"# ⏰ Data: {datetime.now(SAO_PAULO_TZ).strftime('%d/%m/%Y %H:%M:%S')}\n")
                        f.write(f"# =====================================\n\n")
                        for result in cached_results:
                            f.write(result + '\n')

                    # Criar arquivo formatado
                    with open(arquivo_formatado, 'w', encoding='utf-8') as f:
                        f.write(f"{'='*80}\n")
                        f.write(f"{'🤖 OLHOS DE CRISTO BOT - RESULTADOS DE BUSCA 🤖':^80}\n")
                        f.write(f"{'='*80}\n")
                        f.write(f"📱 Telegram: @Olhosdecristo_bot\n")
                        f.write(f"🌐 Domínio Pesquisado: {url}\n")
                        f.write(f"⏰ Data da Busca: {datetime.now(SAO_PAULO_TZ).strftime('%d/%m/%Y %H:%M:%S')}\n")
                        f.write(f"🎯 Desenvolvido por: @Tequ1ladoxxado\n")
                        f.write(f"✨ Bot Premium de Buscas Privadas\n")
                        f.write(f"{'='*80}\n\n")
                        f.write(f"📊 RESULTADOS ENCONTRADOS:\n\n")
                        
                        for linha in cached_results:
                            if ':' in linha:
                                partes = linha.split(':', 1)
                                email, senha = partes[0].strip(), partes[1].strip()
                                f.write(f"🔹 URL: {url}\n")
                                f.write(f"📧 EMAIL: {email}\n")
                                f.write(f"🔐 SENHA: {senha}\n")
                                f.write(f"📍 FONTE: CACHE\n")
                                f.write(f"{'-'*50}\n\n")

                    print(f"[CACHE HIT] {url} - {len(cached_results)} resultados retornados do cache!")
                    return arquivo_raw, arquivo_formatado

                # Cache MISS - buscar na API externa E no banco local
                print(f"[CACHE MISS] {url} - Buscando na API externa e banco local...")

                # Buscar no banco local primeiro (mais rápido)
                search_term = url.lower()
                subdomain_pattern = f"%.{search_term}"
                db_results = []

                try:
                    with sqlite3.connect(DB_FILE) as conn:
                        cursor = conn.cursor()
                        query = """
                            SELECT login_data 
                            FROM logins 
                            WHERE LOWER(domain) = ? OR LOWER(domain) LIKE ?
                            ORDER BY CASE 
                                WHEN LOWER(domain) = ? THEN 0 
                                WHEN LOWER(domain) LIKE ? THEN 1 
                                ELSE 2 
                            END
                            LIMIT 15000
                        """
                        params = (search_term, subdomain_pattern, search_term, subdomain_pattern)
                        cursor.execute(query, params)
                        db_results = [row[0] for row in cursor.fetchall()]

                    print(f"[DB SEARCH] {url} - {len(db_results)} logins encontrados no banco local")
                except Exception as db_error:
                    print(f"[DB SEARCH] ❌ Erro no banco local: {db_error}")
                    db_results = []

                # Buscar na API externa com tratamento robusto
                api_results = []
                try:
                    search_instance = LoginSearch(url, id_user, pasta_temp, tasks_canceladas[hash_nome], contador_callback, limite_max=80000, search_term=search_term)
                    arquivo_raw, arquivo_formatado = search_instance.buscar()

                    # Ler resultados da API externa
                    if os.path.exists(arquivo_raw):
                        with open(arquivo_raw, 'r', encoding='utf-8') as f:
                            api_results = [linha.strip() for linha in f if linha.strip()]
                    
                    print(f"[API SEARCH] {url} - {len(api_results)} logins encontrados na API")
                    
                except Exception as api_error:
                    print(f"[API SEARCH] ❌ Erro na API: {api_error}")
                    logger.error(f"Erro na API externa: {api_error}")
                    api_results = []

                # Combinar todos os resultados (API + Banco Local)
                all_results = list(api_results)  # Começar com API externa

                # Adicionar resultados do banco local que não estão na API
                for db_result in db_results:
                    if db_result not in all_results:
                        all_results.append(db_result)

                # Garantir que arquivos existem com nome baseado na URL
                arquivo_raw = os.path.join(pasta_temp, f"{url_clean}_logins.txt")
                arquivo_formatado = os.path.join(pasta_temp, f"{url_clean}_formatado.txt")

                # Recriar arquivos com resultados combinados
                with open(arquivo_raw, 'w', encoding='utf-8') as f:
                    for result in all_results:
                        f.write(result + '\n')

                with open(arquivo_formatado, 'w', encoding='utf-8') as f:
                    for linha in all_results:
                        if ':' in linha:
                            partes = linha.split(':', 1)
                            email, senha = partes[0].strip(), partes[1].strip()
                            f.write(f"\u2022 EMAIL: {email}\n\u2022 SENHA: {senha}\n\n")

                # Atualizar contador final
                contador_callback(len(all_results))

                # Adicionar resultados combinados ao cache apenas se a busca foi completada
                search_completed = not tasks_canceladas[hash_nome].get('cancelled', False)
                if all_results and search_completed:
                    cache_inteligente.set(url, all_results, search_completed=True)
                    print(f"[CACHE SET] {url} - {len(all_results)} resultados combinados adicionados ao cache")
                elif not search_completed:
                    print(f"[CACHE SKIP] {url} - Busca cancelada, não adicionando ao cache")

                print(f"[COMBINED SEARCH] {url} - {len(api_results)} da API + {len(db_results)} do banco = {len(all_results)} total!")
                return arquivo_raw, arquivo_formatado

            except Exception as e:
                logger.error(f"Erro crítico na busca: {e}")
                print(f"[SEARCH ERROR] ❌ Erro crítico: {e}")
                
                # Criar arquivos vazios em caso de erro
                arquivo_raw = os.path.join(pasta_temp, f"{id_user}.txt")
                arquivo_formatado = os.path.join(pasta_temp, f"{id_user}_formatado.txt")

                try:
                    with open(arquivo_raw, 'w', encoding='utf-8') as f:
                        f.write("")
                    with open(arquivo_formatado, 'w', encoding='utf-8') as f:
                        f.write("")
                except Exception as file_error:
                    logger.error(f"Erro ao criar arquivos vazios: {file_error}")

                return arquivo_raw, arquivo_formatado

        arquivo_raw, arquivo_formatado = await asyncio.to_thread(buscar_wrapper)

        tarefa_editar.cancel()
        try:
            await tarefa_editar
        except asyncio.CancelledError:
            pass

        # Verificar se houve pausa automática
        if 'pause_at' in tasks_canceladas[hash_nome]:
            pause_count = tasks_canceladas[hash_nome]['pause_at']
            await msg_busca.edit(
                f"⏸️ 𝗣𝗮𝘂𝘀𝗮 𝗔𝘂𝘁𝗼𝗺𝗮́𝘁𝗶𝗰𝗮 𝗮𝗼𝘀 𝟮𝟬𝗸!\n\n"
                f"🎯 {pause_count:,} logins encontrados até agora\n"
                f"🌐 Domínio: {url}\n"
                f"⚡ Pausa automática ativada para evitar sobrecarga\n\n"
                f"📋 O que deseja fazer?\n\n"
                f"🔄 **Continuar Busca** - Buscar mais 20k logins\n"
                f"🛑 **Parar Aqui** - Finalizar com {pause_count:,} logins\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"💻 By: Tequ1la".replace(",", "."),
                buttons=[
                    [Button.inline("🔄 Continuar Busca", data=f"continue_search:{id_user}"),
                     Button.inline(f"🛑 Parar com {pause_count:,}".replace(",", "."), data=f"stop_at_pause:{id_user}")],
                    [Button.inline("❌ Cancelar Tudo", data=f"cancel:{id_user}")]
                ]
            )
            usuarios_bloqueados.discard(id_user)
            return

        # Calcular tempo total de busca
        total_search_time = time.time() - search_start_time
        if total_search_time < 60:
            total_time_str = f"{total_search_time:.1f}s"
        else:
            minutes = int(total_search_time // 60)
            seconds = int(total_search_time % 60)
            total_time_str = f"{minutes}m {seconds}s"

        qtd_logins = 0
        if os.path.exists(arquivo_raw):
            with open(arquivo_raw, "r", encoding="utf-8") as f:
                qtd_logins = sum(1 for _ in f)

        if qtd_logins == 0:
            await msg_busca.edit(f"❌ 𝗡𝗲𝗻𝗵𝘂𝗺 𝗿𝗲𝘀𝘂𝗹𝘁𝗮𝗱𝗼 𝗳𝗼𝗶 𝗲𝗻𝗰𝗼𝗻𝘁𝗿𝗮𝗱𝗼!\n\n📝 Tente com outro domínio\n⏱️ Tempo de busca: {total_time_str}\n\n🤖 @Olhosdecristo_bot")
            shutil.rmtree(pasta_temp, ignore_errors=True)
            usuarios_bloqueados.discard(id_user)
            return

        # Adicionar ao histórico de buscas
        add_search_to_history(id_user, url, qtd_logins)

        relatorio = RelatorioPremium(nome, id_user, now, url, qtd_logins)
        caminho_relatorio = os.path.join(RESULTS_DIR, f"relatorio_{hash_nome}.png")

        try:
            relatorio.gerar_relatorio()
        except Exception as e:
            logger.error(f"Erro ao gerar relatório: {e}")
            with open(caminho_relatorio, 'w') as f:
                f.write("Mock report file")

        await msg_busca.delete()

        # Calcular velocidade média
        if total_search_time > 0:
            avg_speed = qtd_logins / total_search_time
            if avg_speed > 1000:
                speed_display = f"{avg_speed/1000:.1f}k/s"
            else:
                speed_display = f"{avg_speed:.0f}/s"
        else:
            speed_display = "Instantâneo"

        # Validar ID do evento para evitar overflow
        safe_event_id = None
        try:
            if hasattr(event, 'id') and event.id:
                if -2147483648 <= event.id <= 2147483647:
                    safe_event_id = event.id
        except:
            pass

        await client.send_message(
            event.chat_id,
            f"✅ 𝗕𝘂𝘀𝗰𝗮 𝗖𝗼𝗻𝗰𝗹𝘂í𝗱𝗮!\n\n"
            f"🎯 Resultados encontrados: {qtd_logins:,}\n"
            f"🌐 Domínio: {url}\n"
            f"⏱️ Tempo total: {total_time_str}\n"
            f"🚀 Velocidade média: {speed_display}\n\n"
            f"📋 Escolha o formato de download:\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💻 By: Tequ1la | @Olhosdecristo_bot".replace(",", "."),
            buttons=[
                [Button.inline("📝 USER:PASS", data=f"format1:{id_user}"),
                 Button.inline("📋 FORMATADO", data=f"format2:{id_user}")],
                [Button.inline("📊 JSON Export", data=f"export_json:{id_user}"),
                 Button.inline("⭐ Favoritar", data=f"add_to_favorites:{url}")],
                [Button.inline("❌ CANCELAR", data=f"cancel:{id_user}")]
            ],
            reply_to=safe_event_id
        )

        usuarios_bloqueados.discard(id_user)

        if os.path.exists(caminho_relatorio):
            os.remove(caminho_relatorio)

    except Exception as e:
        logger.error(f"Erro no search_handler: {e}")
        await event.reply("❌ Erro interno durante a busca.")
        usuarios_bloqueados.discard(event.sender_id)

# Helper para criar a barra de progresso.
def create_progress_bar(progress: float, length: int = 10) -> str:
    filled_len = int(length * progress)
    bar = '█' * filled_len + '─' * (length - filled_len)
    return f"[{bar}]"

@client.on(events.NewMessage(func=lambda e: not e.file and not e.message.message.startswith('/') and e.is_private))
async def unrecognized_message_handler(event):
    """Handler para mensagens não reconhecidas"""
    try:
        sender = await event.get_sender()
        user_id = sender.id
        message_text = event.message.message.strip()
        
        # Verificar se é uma URL simples ou texto não reconhecido
        if message_text and len(message_text) > 0 and len(message_text) <= 100:  # Limitar tamanho
            # Tentar detectar se é uma URL ou domínio
            url_detected = detectar_dominio_inteligente(message_text)
            
            if url_detected:
                # É uma URL/domínio válido, sugerir comando de busca
                suggestion_text = (
                    f"🔍 **Detectei um domínio válido!**\n\n"
                    f"📝 Você digitou: `{message_text}`\n"
                    f"🌐 Domínio detectado: `{url_detected}`\n\n"
                    f"💡 **Para buscar logins, use:**\n"
                    f"`/search {message_text}`\n\n"
                    f"📋 **Ou veja todos os comandos:**\n"
                    f"`/comandos`\n\n"
                    f"🤖 @Olhosdecristo_bot"
                )
                
                buttons = [
                    [Button.inline(f"🔍 Buscar {url_detected}", data=f"quick_search:{message_text}")],
                    [Button.inline("📋 Ver Comandos", data=f"show_commands:{user_id}")],
                    [Button.inline("❌ Apagar", data=f"apagarmensagem:{user_id}")]
                ]
            else:
                # Mensagem não reconhecida
                suggestion_text = (
                    f"❓ **Mensagem não reconhecida**\n\n"
                    f"📝 Você digitou: `{message_text[:50]}{'...' if len(message_text) > 50 else ''}`\n\n"
                    f"💡 **Comandos disponíveis:**\n"
                    f"• `/start` - Menu principal\n"
                    f"• `/search <dominio>` - Buscar logins\n"
                    f"• `/reset` - Cancelar busca e deixar o bot mais leve\n"
                    f"• `/comandos` - Ver todos os comandos\n\n"
                    f"🔍 **Exemplo de busca:**\n"
                    f"`/search netflix`\n"
                    f"`/search google.com`\n\n"
                    f"⚠️ **Dica:** Use `/reset` para cancelar buscas ativas e otimizar o bot\n\n"
                    f"🤖 @Olhosdecristo_bot"
                )
                
                buttons = [
                    [Button.inline("📋 Ver Comandos", data=f"show_commands:{user_id}")],
                    [Button.inline("🏠 Menu Principal", data=f"back_to_start")],
                    [Button.inline("❌ Apagar", data=f"apagarmensagem:{user_id}")]
                ]
            
            await event.respond(
                suggestion_text,
                buttons=buttons,
                parse_mode='Markdown'
            )
        
    except Exception as e:
        logger.error(f"Erro no unrecognized_message_handler: {e}")
        # Silencioso para não spammar

@client.on(events.NewMessage(func=lambda e: e.file))
async def file_upload_handler(event):
    if not (event.document and event.document.mime_type == 'text/plain'):
        await event.respond("⚠️ **Arquivo Inválido.** Envie apenas arquivos no formato `.txt`.")
        return

    # Verificar tamanho do arquivo (limite de 1GB)
    file_size = event.document.size
    max_size = 1024 * 1024 * 1024  # 1GB em bytes

    if file_size > max_size:
        size_mb = file_size / (1024 * 1024)
        await event.respond(f"❌ **Arquivo muito grande!**\n\n📊 Tamanho: {size_mb:.1f}MB\n🚫 Limite máximo: 1GB (1024MB)\n\n💡 Divida o arquivo em partes menores.")
        return

    msg = await event.respond("🚀 **SISTEMA ULTRA-RÁPIDO ATIVADO!**\n\n📥 Baixando arquivo com tecnologia otimizada...")
    temp_path = await client.download_media(event.message.document, file=RESULTS_DIR)

    total_lines = 0
    added_count = 0
    duplicate_count = 0
    chunk = []
    processed_domains = set()  # Para tracking de domínios únicos

    # Sistema de chunk ULTRA otimizado para dispositivos móveis potentes (S24 Ultra)
    if file_size > 500 * 1024 * 1024:  # > 500MB
        CHUNK_SIZE = MOBILE_CHUNK_SIZE  # Aproveitar processador potente
        UPDATE_FREQUENCY = MOBILE_UPDATE_FREQ  # Otimizado para mobile
    elif file_size > 100 * 1024 * 1024:  # > 100MB
        CHUNK_SIZE = 200000  # Chunks grandes para Snapdragon 8 Gen 3
        UPDATE_FREQUENCY = 30000
    elif file_size > 10 * 1024 * 1024:  # > 10MB
        CHUNK_SIZE = 150000  # Chunks médios otimizados
        UPDATE_FREQUENCY = 15000
    else:
        CHUNK_SIZE = 75000  # Chunk maior que padrão para mobile potente
        UPDATE_FREQUENCY = 7500

    last_update_time = datetime.now()
    last_progress = 0
    lines_since_update = 0

    try:
        await msg.edit("⚡ **TURBO MODE ATIVADO!**\n\n🔍 Analisando arquivo com IA otimizada...")

        # Contagem super rápida de linhas
        start_count = datetime.now()
        with open(temp_path, 'rb') as file:
            total_lines = sum(1 for _ in file)
        count_time = (datetime.now() - start_count).total_seconds()

        if total_lines == 0:
            await msg.edit("⚠️ O arquivo parece estar vazio.")
            return

        # Estimativa inteligente baseada em performance real
        lines_per_second_estimate = 75000 if file_size > 100 * 1024 * 1024 else 50000
        estimated_time = max(5, total_lines // lines_per_second_estimate)

        await msg.edit(
            f"🔥 **SUPER PROCESSADOR INICIALIZADO!**\n\n"
            f"📊 **Análise Completa:**\n"
            f"• 📝 Total de Linhas: `{total_lines:,}`\n"
            f"• 💾 Tamanho: `{file_size/(1024*1024):.1f}MB`\n"
            f"• ⚡ Chunk Size: `{CHUNK_SIZE:,}` (ULTRA)\n"
            f"• 🚀 Velocidade Estimada: `{lines_per_second_estimate:,}/seg`\n"
            f"• ⏱️ Tempo Estimado: `~{estimated_time}s`\n"
            f"• 🔍 Análise: `{count_time:.2f}s`\n\n"
            f"🎯 **INICIANDO PROCESSAMENTO TURBINADO...**".replace(",", ".")
        )

        start_time = datetime.now()
        next_update_at = UPDATE_FREQUENCY

        # Configurações de banco otimizadas para performance máxima
        with sqlite3.connect(DB_FILE) as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = OFF")  # Máxima velocidade
            conn.execute("PRAGMA cache_size = 50000")  # Cache gigante
            conn.execute("PRAGMA temp_store = MEMORY")
            conn.execute("PRAGMA locking_mode = EXCLUSIVE")
            conn.execute("BEGIN TRANSACTION")

            cursor = conn.cursor()

            with open(temp_path, 'r', encoding='utf-8', errors='ignore') as file:
                for i, line in enumerate(file, 1):
                    lines_since_update += 1

                    if ':' in line:
                        parts = line.split(':', 1)
                        if len(parts) >= 2:
                            domain = extract_domain_final(parts[0])
                            if domain:
                                processed_domains.add(domain)
                                chunk.append((domain, line.strip()))

                    # Sistema de inserção TURBO com commits otimizados
                    if len(chunk) >= CHUNK_SIZE:
                        try:
                            cursor.executemany("INSERT OR IGNORE INTO logins (domain, login_data) VALUES (?, ?)", chunk)
                            inserted = cursor.rowcount
                            added_count += inserted
                            duplicate_count += len(chunk) - inserted
                            chunk = []

                            # Commit estratégico para performance
                            if added_count % (CHUNK_SIZE * 3) == 0:
                                conn.commit()
                                conn.execute("BEGIN TRANSACTION")

                        except Exception as db_error:
                            print(f"[TURBO DB ERROR] Erro ao inserir chunk: {db_error}")
                            conn.rollback()
                            conn.execute("BEGIN TRANSACTION")

                    # Sistema de atualização ultra-otimizado
                    if i >= next_update_at or i == total_lines:
                        progress_percent = i / total_lines
                        now = datetime.now()
                        elapsed_seconds = (now - start_time).total_seconds()

                        next_update_at = i + UPDATE_FREQUENCY

                        if elapsed_seconds > 0:
                            current_speed = lines_since_update / (now - last_update_time).total_seconds()
                            overall_speed = i / elapsed_seconds
                            remaining_lines = total_lines - i
                            eta_seconds = int(remaining_lines / overall_speed) if overall_speed > 0 else 0

                            # ETA formatado
                            if eta_seconds > 3600:
                                eta_str = f"{eta_seconds // 3600}h {(eta_seconds % 3600) // 60}m"
                            elif eta_seconds > 60:
                                eta_str = f"{eta_seconds // 60}m {eta_seconds % 60}s"
                            else:
                                eta_str = f"{eta_seconds}s"

                            # Velocidade formatada
                            if current_speed > 1000:
                                speed_str = f"{current_speed/1000:.1f}K/s"
                            else:
                                speed_str = f"{current_speed:.0f}/s"

                            # Barra de progresso TURBO
                            bar_length = 30
                            filled = int(bar_length * progress_percent)
                            progress_bar = "🟢" * filled + "⚫" * (bar_length - filled)

                            # Performance indicator
                            if current_speed > 75000:
                                perf_indicator = "🚀 ULTRA VELOCIDADE"
                            elif current_speed > 50000:
                                perf_indicator = "⚡ ALTA VELOCIDADE"
                            elif current_speed > 25000:
                                perf_indicator = "🔥 BOA VELOCIDADE"
                            else:
                                perf_indicator = "📊 PROCESSANDO"

                            status_text = (
                                f"🔥 **TURBO PROCESSOR - {progress_percent*100:.1f}%**\n\n"
                                f"{progress_bar}\n"
                                f"**{progress_percent*100:.1f}%** completo | {perf_indicator}\n\n"
                                f"📈 **Estatísticas em Tempo Real:**\n"
                                f"• 🔢 Processadas: `{i:,}` / `{total_lines:,}`\n"
                                f"• ✅ Adicionadas: `{added_count:,}`\n"
                                f"• 🔄 Duplicatas: `{duplicate_count:,}`\n"
                                f"• 🌐 Domínios Únicos: `{len(processed_domains):,}`\n\n"
                                f"⚡ **Performance Ultra:**\n"
                                f"• 🚀 Velocidade Atual: `{speed_str}`\n"
                                f"• 📊 Velocidade Média: `{overall_speed:.0f}/s`\n"
                                f"• ⏱️ Tempo Restante: `{eta_str}`\n"
                                f"• 💾 Chunk: `{CHUNK_SIZE:,}` linhas\n\n"
                                f"💿 **Arquivo:** `{file_size/(1024*1024):.1f}MB` | Modo TURBO ativado!".replace(",", ".")
                            )

                            try:
                                await msg.edit(status_text)
                                print(f"[TURBO MODE] {progress_percent*100:.1f}% - {overall_speed:.0f}/s - {added_count:,} adicionados")
                            except Exception as edit_error:
                                if "not modified" not in str(edit_error).lower():
                                    print(f"[TURBO] Erro ao editar: {edit_error}")

                        last_update_time = now
                        lines_since_update = 0

            # Processar chunk final
            if chunk:
                try:
                    cursor.executemany("INSERT OR IGNORE INTO logins (domain, login_data) VALUES (?, ?)", chunk)
                    inserted = cursor.rowcount
                    added_count += inserted
                    duplicate_count += len(chunk) - inserted
                except Exception as db_error:
                    print(f"[TURBO] Erro no chunk final: {db_error}")

            # Commit final
            conn.commit()

        # Estatísticas finais ÉPICAS
        total_elapsed = (datetime.now() - start_time).total_seconds()

        if total_elapsed > 3600:
            time_str = f"{int(total_elapsed // 3600)}h {int((total_elapsed % 3600) // 60)}m"
        elif total_elapsed > 60:
            time_str = f"{int(total_elapsed // 60)}m {int(total_elapsed % 60)}s"
        else:
            time_str = f"{total_elapsed:.1f}s"

        success_rate = (added_count / total_lines * 100) if total_lines > 0 else 0
        final_speed = total_lines / total_elapsed if total_elapsed > 0 else 0

        # Classificação de performance
        if final_speed > 100000:
            performance_grade = "🏆 LEGENDARY"
        elif final_speed > 75000:
            performance_grade = "🥇 ÉPICO"
        elif final_speed > 50000:
            performance_grade = "🥈 EXCELENTE"
        elif final_speed > 25000:
            performance_grade = "🥉 MUITO BOM"
        else:
            performance_grade = "✅ BOM"

        final_message = (
            f"🎉 **PROCESSAMENTO TURBO CONCLUÍDO!**\n\n"
            f"🔥🔥🔥🔥🔥🔥🔥🔥🔥🔥🔥🔥🔥🔥🔥🔥🔥🔥🔥🔥🔥🔥🔥🔥🔥🔥🔥🔥🔥🔥\n"
            f"**100%** - MISSÃO CUMPRIDA! {performance_grade}\n\n"
            f"📊 **RELATÓRIO FINAL ÉPICO:**\n"
            f"• 🚀 **Logins Adicionados:** `{added_count:,}`\n"
            f"• 📝 **Linhas Processadas:** `{total_lines:,}`\n"
            f"• 🔄 **Duplicatas Filtradas:** `{duplicate_count:,}`\n"
            f"• 🌐 **Domínios Únicos:** `{len(processed_domains):,}`\n"
            f"• ✅ **Taxa de Sucesso:** `{success_rate:.1f}%`\n"
            f"• ⚡ **Velocidade Final:** `{final_speed:.0f} linhas/seg`\n"
            f"• ⏱️ **Tempo Record:** `{time_str}`\n"
            f"• 💾 **Arquivo Processado:** `{file_size/(1024*1024):.1f}MB`\n\n"
            f"🏆 **CLOUD TURBINADA ADICIONADA COM SUCESSO!**\n"
            f"🚀 **SISTEMA OTIMIZADO PARA ARQUIVOS ATÉ 1GB**\n"
            f"⚡ **MODO TURBO: VELOCIDADE MÁXIMA ATINGIDA!**".replace(",", ".")
        )
        await msg.edit(final_message)

        # Log super detalhado
        await log_action(f"TURBO CLOUD: {added_count:,} logins adicionados de {total_lines:,} linhas em {time_str} - Velocidade: {final_speed:.0f}/s")

    except Exception as e:
        error_msg = (
            f"❌ **ERRO NO SISTEMA TURBO:**\n\n"
            f"`{str(e)}`\n\n"
            f"🔧 **Soluções Rápidas:**\n"
            f"• Verifique o formato do arquivo (email:senha)\n"
            f"• Tente arquivos menores se persistir\n"
            f"• Reinicie o bot se necessário\n"
            f"• Contate o suporte técnico\n\n"
            f"🚀 **O sistema TURBO está sempre evoluindo!**"
        )
        await msg.edit(error_msg)
        await log_action(f"TURBO ERROR: {e}")
        print(f"[TURBO CLOUD ERROR] {e}")
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


@client.on(events.NewMessage(pattern=r'/(ban|cancelar|unban|autorizar|info|reload_admins) ?(\S+)?(.*)'))
async def admin_commands_handler(event):
    # LIBERADO PARA TODOS OS USUÁRIOS

    command = event.pattern_match.group(1)
    target_id_str = event.pattern_match.group(2)
    args = event.pattern_match.group(3).strip() if event.pattern_match.group(3) else ""

    if command == "reload_admins":
        reload_admins()
        await event.respond(f"✅ Lista de administradores recarregada! Admins ativos: {len(ADMIN_IDS)}")
        return

    if not target_id_str or not target_id_str.isdigit():
        await event.respond("⚠️ O ID do usuário deve ser um número."); return
    target_id = int(target_id_str)

    if command == "ban":
        if target_id in ADMIN_IDS: await event.respond("Não é possível banir um admin."); return
        ban_user(target_id)
        await event.respond(f"Usuário `{target_id}` foi banido.", parse_mode='Markdown')
    elif command == "unban":
        unban_user(target_id)
        await event.respond(f"Usuário `{target_id}` foi desbanido.", parse_mode='Markdown')
    elif command == "cancelar":
        cancel_plan(target_id)
        await event.respond(f"O plano do usuário `{target_id}` foi cancelado.", parse_mode='Markdown')
    elif command == "info":
        info = f"**🔍 Informações sobre:** `{target_id}`\n\n"
        if is_banned(target_id): info += "🚫 **Status:** `Banido`\n"
        elif is_authorized(target_id): info += f"✅ **Status:** `Ativo`\n   - **Expira em:** {get_user_expiry_date(target_id)}\n"
        else: info += "❌ **Status:** `Inativo`\n"
        stats = get_affiliate_stats(target_id)
        info += f"\n**Afiliado:**\n  - **Indicados:** {stats['referrals']}\n  - **Conversões:** {stats['conversions']}\n  - **Saldo:** R$ {stats['earnings']:.2f}"
        await event.respond(info, parse_mode='Markdown')
    elif command == "autorizar":
        if not args:
            await event.respond("⚠️ Uso: `/autorizar <ID> <tempo>` (ex: 7d, 12h)"); return
        try:
            value = int(args[:-1])
            unit = args[-1].lower()
            if unit == 'd': delta, unit_str = timedelta(days=value), "dia(s)"
            elif unit == 'h': delta, unit_str = timedelta(hours=value), "hora(s)"
            else: await event.respond("⚠️ Unidade de tempo inválida. Use 'd' ou 'h'."); return

            authorize_user_with_delta(target_id, delta)
            await event.respond(f"✅ Usuário `{target_id}` autorizado por **{value} {unit_str}**.", parse_mode='Markdown')
        except (ValueError, IndexError):
            await event.respond("⚠️ Formato de tempo inválido. Ex: `7d` ou `12h`.")

@client.on(events.CallbackQuery)
async def callback_handler(event):
    user_id = event.sender_id
    data = event.data.decode('utf-8')
    await event.answer()

    # Handlers globais (para todos os usuários)
    if data == 'redeem_token_prompt':
        await event.respond("🚀 𝗢𝗸! 𝗘𝗻𝘃𝗶𝗲 𝘀𝗲𝘂 𝘁𝗼𝗸𝗲𝗻 𝗻𝗼 𝗰𝗵𝗮𝘁:\n\n💡 Exemplo: /resgatar SEU-TOKEN-AQUI")
        return

    if data == 'group_plans':
        message = (
            "💎 𝗣𝗹𝗮𝗻𝗼𝘀 𝗘𝘅𝗰𝗹𝘂𝘀𝗶𝘃𝗼𝘀 𝗽𝗮𝗿𝗮 𝗚𝗿𝘂𝗽𝗼𝘀! 💎\n\n"
            "🚀 Transforme sua equipe com nossa tecnologia avançada!\n"
            "⚡ Cache inteligente para resultados instantâneos\n"
            "🎯 Precisão e velocidade incomparáveis\n\n"
            "📦 𝗡𝗼𝘀𝘀𝗼𝘀 𝗣𝗮𝗰𝗼𝘁𝗲𝘀:\n\n"
            "🔵 Plano Mensal: R$ 35,00\n"
            "🟢 Plano Bimestral: R$ 55,00\n"
            "🟡 Plano Trimestral: R$ 70,00\n\n"
            "✨ Plano Vitalício: Oferta personalizada!\n\n"
            "💬 Interessado? Clique abaixo para negociar:\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "💻 By: Tequ1la"
        )
        await event.edit(message, buttons=[[Button.url("💬 Falar com o Gerente", "https://t.me/Tequ1ladoxxado")], [Button.inline("⬅️ Voltar", b"back_to_start")]])
        return

    if data == 'back_to_start':
        await send_start_message(event)
        return
    
    if data == 'start_trial':
        # Simular comando /teste
        from types import SimpleNamespace
        fake_event = SimpleNamespace()
        fake_event.respond = event.edit
        fake_event.get_sender = event.get_sender
        fake_event.sender_id = user_id
        await teste_command(fake_event)
        return
    
    if data == 'trial_used_info':
        await event.edit(
            "🚫 **Teste já utilizado**\n\n"
            "Você já usou seu período de teste gratuito de 30 minutos.\n\n"
            "💎 **Para ter acesso completo:**\n"
            "• Adquira um plano premium\n"
            "• Use um token se possuir\n\n"
            "💬 **Contate o suporte:**\n"
            "@Tequ1ladoxxado\n\n"
            "🤖 @Olhosdecristo_bot",
            buttons=[
                [Button.inline("💎 Ver Planos", b"group_plans")],
                [Button.inline("🔑 Resgatar Token", b"redeem_token_prompt")],
                [Button.inline("⬅️ Voltar", b"back_to_start")]
            ]
        )
        return
    
    if data == 'help_visitor':
        await event.edit(
            "❓ **Ajuda para Visitantes**\n\n"
            "🆓 **Teste Gratuito:**\n"
            "• Use `/teste` para 30 min de acesso\n"
            "• Teste todas as funcionalidades\n"
            "• Apenas 1 teste por usuário\n\n"
            "🔑 **Comandos Disponíveis:**\n"
            "• `/start` - Menu principal\n"
            "• `/teste` - Ativar teste gratuito\n"
            "• `/ping` - Teste de latência\n"
            "• `/resgatar <token>` - Resgatar token\n\n"
            "💎 **Planos Premium:**\n"
            "• Acesso ilimitado\n"
            "• Suporte prioritário\n"
            "• Sem limitações de tempo\n\n"
            "🤖 @Olhosdecristo_bot",
            buttons=[
                [Button.inline("🆓 Ativar Teste", b"start_trial")],
                [Button.inline("💎 Ver Planos", b"group_plans")],
                [Button.inline("⬅️ Voltar", b"back_to_start")]
            ]
        )
        return

    # Handlers de busca/arquivo
    hash_nome = str(user_id)

    if data.startswith("cancelarbusca:"):
        cancel_user_id = int(data.split(":")[1])
        if user_id != cancel_user_id:
            await event.answer("APENAS O USUÁRIO QUE INICIOU A BUSCA PODE CANCELÁ-LA.", alert=True)
            return

        hash_nome_cancel = str(cancel_user_id)
        if hash_nome_cancel in tasks_canceladas:
            tasks_canceladas[hash_nome_cancel]['cancelled'] = True

        usuarios_bloqueados.discard(cancel_user_id)
        await event.answer("SUA BUSCA FOI CANCELADA COM SUCESSO!", alert=True)
        await event.delete()

    elif data.startswith("apagarmensagem:"):
        delete_user_id = int(data.split(":")[1])
        if user_id != delete_user_id:
            await event.answer("APENAS O USUÁRIO ORIGINAL PODE APAGAR A MENSAGEM.", alert=True)
            return
        await event.delete()

    if data.startswith("show_commands:"):
        target_user = int(data.split(":")[1])
        if user_id != target_user:
            await event.answer("APENAS O USUÁRIO ORIGINAL PODE VER OS COMANDOS.", alert=True)
            return
        
        # Simular o comando /comandos
        from types import SimpleNamespace
        fake_event = SimpleNamespace()
        fake_event.respond = event.edit
        fake_event.get_sender = event.get_sender
        await comandos_handler(fake_event)
        return

    if data == "show_favorites":
        # Simular comando /favoritos
        from types import SimpleNamespace
        fake_event = SimpleNamespace()
        fake_event.respond = event.edit
        fake_event.get_sender = event.get_sender
        fake_event.sender_id = user_id
        await favoritos_command(fake_event)
        return

    if data.startswith("search_fav:"):
        # Busca rápida de favorito
        domain = data.split(":", 1)[1]
        
        # Simular comando de busca
        from types import SimpleNamespace
        fake_event = SimpleNamespace()
        fake_event.pattern_match = SimpleNamespace()
        fake_event.pattern_match.group = lambda x: domain
        fake_event.sender_id = user_id
        fake_event.get_sender = event.get_sender
        fake_event.respond = lambda *args, **kwargs: client.send_message(user_id, *args, **kwargs)
        fake_event.reply = lambda *args, **kwargs: client.send_message(user_id, *args, **kwargs)
        fake_event.chat_id = event.chat_id
        fake_event.id = event.id
        
        await event.delete()
        await search_handler(fake_event)
        return

    if data.startswith("add_to_favorites:"):
        # Adicionar domínio aos favoritos após busca
        domain = data.split(":", 1)[1]
        add_favorite_domain(user_id, domain)
        await event.answer(f"⭐ {domain} adicionado aos favoritos!", alert=True)
        return

    if data.startswith("export_json:"):
        # Exportar resultados em JSON
        target_user = int(data.split(":")[1])
        if user_id != target_user:
            await event.answer("APENAS O USUÁRIO ORIGINAL PODE EXPORTAR.", alert=True)
            return
        
        domain = urls_busca.get(target_user, "unknown")
        pasta = os.path.join(TEMP_DIR, str(target_user))
        raw_file = os.path.join(pasta, f"{target_user}.txt")
        
        if os.path.exists(raw_file):
            with open(raw_file, 'r', encoding='utf-8') as f:
                results = [line.strip() for line in f if line.strip()]
            
            json_file = export_search_results_json(target_user, domain, results)
            
            await client.send_file(
                event.chat_id,
                file=json_file,
                caption=f"📊 **Exportação JSON**\n\n🌐 Domínio: {domain}\n📝 Total: {len(results)} logins\n📅 Exportado: {datetime.now(SAO_PAULO_TZ).strftime('%d/%m/%Y %H:%M')}\n\n🤖 @Olhosdecristo_bot",
                buttons=[[Button.inline("❌ Apagar", data=f"deletefile:{target_user}")]]
            )
            
            await event.delete()
            
            # Limpar arquivo temporário
            if os.path.exists(json_file):
                os.remove(json_file)
        else:
            await event.answer("Arquivo não encontrado!", alert=True)
        return

    if data.startswith("quick_search:"):
        # LIBERADO PARA TODOS OS USUÁRIOS - SEM VERIFICAÇÃO DE PLANO
        
        search_term = data.split(":", 1)[1]
        
        # Simular comando de busca
        from types import SimpleNamespace
        fake_event = SimpleNamespace()
        fake_event.pattern_match = SimpleNamespace()
        fake_event.pattern_match.group = lambda x: search_term
        fake_event.sender_id = user_id
        fake_event.get_sender = event.get_sender
        fake_event.respond = lambda *args, **kwargs: client.send_message(user_id, *args, **kwargs)
        fake_event.reply = lambda *args, **kwargs: client.send_message(user_id, *args, **kwargs)
        fake_event.chat_id = event.chat_id
        fake_event.id = event.id
        
        await event.delete()
        await search_handler(fake_event)
        return

    if data.startswith("ver_progresso:"):
        target_user = int(data.split(":")[1])
        if user_id != target_user:
            await event.answer("APENAS O USUÁRIO ORIGINAL PODE VER O PROGRESSO.", alert=True)
            return
        
        # Verificar se tem busca ativa
        hash_nome = str(target_user)
        if hash_nome in tasks_canceladas and not tasks_canceladas[hash_nome].get('cancelled', False):
            busca_atual = urls_busca.get(target_user, "desconhecida")
            
            # Calcular tempo decorrido (estimativa)
            import time
            tempo_estimado = "em andamento"
            
            progress_message = (
                f"📊 **Status da Busca Atual**\n\n"
                f"🔍 **Domínio:** {busca_atual}\n"
                f"⏱️ **Status:** Processando...\n"
                f"🚀 **Tempo:** {tempo_estimado}\n\n"
                f"💡 **Opções:**\n"
                f"• Use `/reset` para cancelar\n"
                f"• Aguarde a conclusão para ver resultados\n\n"
                f"⚡ **Dica:** Buscas grandes podem levar alguns minutos\n\n"
                f"🤖 @Olhosdecristo_bot"
            )
            
            await event.edit(
                progress_message,
                buttons=[
                    [Button.inline("🔴 Cancelar Busca", data=f"cancelarbusca:{target_user}")],
                    [Button.inline("🔄 Atualizar Status", data=f"ver_progresso:{target_user}")],
                    [Button.inline("❌ Fechar", data=f"apagarmensagem:{target_user}")]
                ]
            )
        else:
            await event.edit(
                "✅ **Nenhuma busca em andamento**\n\n"
                "Você pode iniciar uma nova busca usando:\n"
                "`/search <dominio>`\n\n"
                "🤖 @Olhosdecristo_bot",
                buttons=[[Button.inline("❌ Fechar", data=f"apagarmensagem:{user_id}")]]
            )
        return

    if data.startswith("continue_search:"):
        target_user = int(data.split(":")[1])
        if user_id != target_user:
            await event.answer("APENAS O USUÁRIO ORIGINAL PODE CONTINUAR A BUSCA.", alert=True)
            return
        
        # Remover flag de pausa e reiniciar busca
        hash_nome = str(target_user)
        if hash_nome in tasks_canceladas and 'pause_at' in tasks_canceladas[hash_nome]:
            pause_count = tasks_canceladas[hash_nome]['pause_at']
            del tasks_canceladas[hash_nome]['pause_at']
            tasks_canceladas[hash_nome]['cancelled'] = False  # Garantir que não está cancelada
            usuarios_bloqueados.add(target_user)
            
            await event.edit(
                f"🔄 𝗥𝗲𝗶𝗻𝗶𝗰𝗶𝗮𝗻𝗱𝗼 𝗕𝘂𝘀𝗰𝗮...\n\n"
                f"⚡ Continuando de onde parou ({pause_count:,} logins já encontrados)\n"
                f"🎯 Buscando mais 20k logins\n\n"
                f"Use /reset se quiser cancelar".replace(",", ".")
            )
            
            # Buscar URL e iniciar nova busca IMEDIATAMENTE
            url = urls_busca.get(target_user, "")
            if url:
                # Criar nova task de busca com prioridade
                asyncio.create_task(continuar_busca_imediata(event, target_user, url, pause_count))
        return

    if data.startswith("stop_at_pause:"):
        target_user = int(data.split(":")[1])
        if user_id != target_user:
            await event.answer("APENAS O USUÁRIO ORIGINAL PODE PARAR A BUSCA.", alert=True)
            return
        
        # Finalizar busca com resultados atuais
        hash_nome = str(target_user)
        if hash_nome in tasks_canceladas and 'pause_at' in tasks_canceladas[hash_nome]:
            pause_count = tasks_canceladas[hash_nome]['pause_at']
            
            await event.edit(
                f"✅ 𝗕𝘂𝘀𝗰𝗮 𝗙𝗶𝗻𝗮𝗹𝗶𝘇𝗮𝗱𝗮!\n\n"
                f"🎯 Total de resultados: {pause_count:,}\n"
                f"🌐 Domínio: {urls_busca.get(target_user, 'N/A')}\n\n"
                f"📋 Escolha o formato de download:\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"💻 By: Tequ1la".replace(",", "."),
                buttons=[
                    [Button.inline("📝 USER:PASS", data=f"format1:{target_user}"),
                     Button.inline("📋 FORMATADO", data=f"format2:{target_user}")],
                    [Button.inline("❌ CANCELAR", data=f"cancel:{target_user}")]
                ]
            )
        return

    if data.startswith("cancel:"):
        target_user = int(data.split(":")[1])
        if user_id != target_user:
            await event.answer("APENAS O USUÁRIO QUE PEDIU O COMANDO PODE USAR ESSES BOTÕES.\n\nERR_USER_NOT_VERIFIED", alert=True)
            return
        await event.delete()
        return

    if data.startswith("deletefile:"):
        target_user = int(data.split(":")[1])
        if user_id != target_user:
            await event.answer("APENAS O USUÁRIO QUE RECEBEU O ARQUIVO PODE APAGAR.\n\nERR_USER_NOT_VERIFIED", alert=True)
            return
        await event.delete()
        return

    if data.startswith("format1:") or data.startswith("format2:"):
        acao, id_user_btn = data.split(":")
        id_user_btn = int(id_user_btn)

        if user_id != id_user_btn:
            await event.answer("APENAS O USUÁRIO QUE PEDIU O COMANDO PODE USAR ESSES BOTÕES.\n\nERR_USER_NOT_VERIFIED", alert=True)
            return

        pasta = os.path.join(TEMP_DIR, str(id_user_btn))
        
        # Buscar arquivo pelo padrão URL
        url_busca = urls_busca.get(id_user_btn, "resultado")
        url_clean = re.sub(r'[^\w\-_\.]', '_', url_busca)
        if url_clean.startswith('_'):
            url_clean = url_clean[1:]
        if url_clean.endswith('_'):
            url_clean = url_clean[:-1]
        
        nome_arquivo = f"{url_clean}_logins.txt" if acao == "format1" else f"{url_clean}_formatado.txt"
        caminho = os.path.join(pasta, nome_arquivo)
        
        # Fallback para o padrão antigo se não encontrar
        if not os.path.exists(caminho):
            nome_arquivo_old = f"{id_user_btn}.txt" if acao == "format1" else f"{id_user_btn}_formatado.txt"
            caminho_old = os.path.join(pasta, nome_arquivo_old)
            if os.path.exists(caminho_old):
                caminho = caminho_old
                nome_arquivo = nome_arquivo_old

        if not os.path.exists(caminho):
            await event.answer("O ARQUIVO FONTE NÃO FOI ENCONTRADO! TENTE NOVAMENTE MAIS TARDE.\n\nARCHIVE_NOT_FOUND", alert=True)
            return

        await event.delete()
        await asyncio.sleep(0.5)

        sender_entity = await client.get_entity(id_user_btn)
        mention = f"[{sender_entity.first_name}](tg://user?id={id_user_btn})"

        with open(caminho, "r", encoding="utf-8") as f:
            qtd = sum(1 for _ in f)

        caption = f"""☁️ 𝗥𝗲𝘀𝘂𝗹𝘁𝗮𝗱𝗼 𝗘𝗻𝘃𝗶𝗮𝗱𝗼 - 𝗧𝗫𝗧\n\n📊 Quantidade: {qtd:,}\n🌐 URL: {urls_busca.get(id_user_btn, "desconhecida")}\n👤 Solicitado por: {mention}\n\n🤖 @Olhosdecristo_bot""".replace(",", ".")

        safe_reply_to = safe_telegram_id(mensagens_origem.get(id_user_btn))
        
        await client.send_file(
            event.chat_id,
            file=caminho,
            caption=caption,
            buttons=[[Button.inline("❌ Apagar Mensagem", data=f"deletefile:{id_user_btn}")]],
            reply_to=safe_reply_to
        )

        try:
            await client.send_message(MEU_ID, f"""**⚠️ | NOVA CONSULTA DE LOGIN**\n\n**• QUEM FOI:** {mention}\n**• URL:** {urls_busca.get(id_user_btn, "desconhecida")}\n**• QUANTIDADE:** {qtd}\n\n🤖 @Olhosdecristo_bot""")
        except Exception as e:
            logger.error(f"Erro ao notificar admin: {e}")

        shutil.rmtree(pasta, ignore_errors=True)
        return

    # LIBERADO PARA TODOS OS USUÁRIOS - ACESSO ADMIN PARA TODOS
    if True:  # Antiga verificação: user_id in ADMIN_IDS
        if data == 'gen_token_panel':
            buttons = [
                [Button.inline("1 Dia", b"gift_1"), Button.inline("7 Dias", b"gift_7"), Button.inline("30 Dias", b"gift_30")],
                [Button.inline("60 Dias", b"gift_60"), Button.inline("90 Dias", b"gift_90"), Button.inline("Vitalício ✨", b"gift_36500")],
                [Button.inline("⬅️ Voltar ao Painel", b"back_to_admin")]
            ]
            await event.edit("🔑 Selecione a duração do token a ser gerado:", buttons=buttons)
        elif data == 'export_users':
            await event.edit("📊 **Gerando arquivo de usuários...**\n\nPor favor, aguarde.")
            
            try:
                # Buscar todos os usuários do banco
                with sqlite3.connect(DB_FILE) as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        SELECT u.user_id, u.first_name, u.username, 
                               CASE WHEN a.expiry_date IS NOT NULL THEN a.expiry_date ELSE 'Não autorizado' END as status,
                               CASE WHEN b.user_id IS NOT NULL THEN 'Banido' ELSE 'Ativo' END as ban_status
                        FROM users u 
                        LEFT JOIN authorizations a ON u.user_id = a.user_id
                        LEFT JOIN blacklist b ON u.user_id = b.user_id
                        ORDER BY u.user_id
                    """)
                    users_data = cursor.fetchall()

                if not users_data:
                    await event.edit("⚠️ Nenhum usuário encontrado no banco de dados.")
                    return

                # Criar arquivo de usuários
                os.makedirs(RESULTS_DIR, exist_ok=True)
                filename = f"usuarios_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
                filepath = os.path.join(RESULTS_DIR, filename)

                total_users = len(users_data)
                authorized_count = 0
                banned_count = 0

                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write("=" * 60 + "\n")
                    f.write("📊 RELATÓRIO COMPLETO DE USUÁRIOS - OLHOSDECRISTO BOT\n")
                    f.write("=" * 60 + "\n")
                    f.write(f"📅 Gerado em: {datetime.now(SAO_PAULO_TZ).strftime('%d/%m/%Y %H:%M:%S')}\n")
                    f.write(f"👥 Total de usuários: {total_users:,}\n\n".replace(",", "."))

                    f.write("🔍 LISTA DETALHADA DE USUÁRIOS:\n")
                    f.write("-" * 60 + "\n\n")

                    for user_id, first_name, username, status, ban_status in users_data:
                        if ban_status == "Banido":
                            banned_count += 1
                        if status != "Não autorizado":
                            authorized_count += 1

                        f.write(f"👤 ID: {user_id}\n")
                        f.write(f"📝 Nome: {first_name or 'N/A'}\n")
                        f.write(f"🏷️ Username: @{username or 'N/A'}\n")
                        f.write(f"📊 Status: {ban_status}\n")
                        
                        if status != "Não autorizado":
                            try:
                                expiry_dt = datetime.fromisoformat(status)
                                if expiry_dt > datetime.now(SAO_PAULO_TZ) + timedelta(days=365*90):
                                    f.write(f"⏰ Plano: Vitalício ✨\n")
                                else:
                                    f.write(f"⏰ Expira: {expiry_dt.strftime('%d/%m/%Y %H:%M')}\n")
                            except:
                                f.write(f"⏰ Plano: {status}\n")
                        else:
                            f.write(f"⏰ Plano: Não autorizado\n")
                        
                        f.write("-" * 40 + "\n")

                    f.write(f"\n📈 ESTATÍSTICAS RESUMIDAS:\n")
                    f.write(f"👥 Total de usuários: {total_users:,}\n".replace(",", "."))
                    f.write(f"✅ Usuários autorizados: {authorized_count:,}\n".replace(",", "."))
                    f.write(f"🚫 Usuários banidos: {banned_count:,}\n".replace(",", "."))
                    f.write(f"📊 Taxa de autorização: {(authorized_count/total_users)*100:.1f}%\n")
                    f.write(f"📊 Taxa de banimento: {(banned_count/total_users)*100:.1f}%\n")

                # Estatísticas para o admin
                stats_text = (
                    f"✅ **Arquivo de usuários gerado com sucesso!**\n\n"
                    f"📊 **Estatísticas:**\n"
                    f"• Total de usuários: `{total_users:,}`\n"
                    f"• Usuários autorizados: `{authorized_count:,}`\n"
                    f"• Usuários banidos: `{banned_count:,}`\n"
                    f"• Taxa de autorização: `{(authorized_count/total_users)*100:.1f}%`\n\n"
                    f"📁 **Arquivo:** `{filename}`\n"
                    f"📅 **Gerado em:** {datetime.now(SAO_PAULO_TZ).strftime('%d/%m/%Y %H:%M:%S')}"
                ).replace(",", ".")

                # Enviar arquivo
                await client.send_file(
                    event.chat_id,
                    file=filepath,
                    caption=stats_text,
                    parse_mode='Markdown',
                    buttons=[[Button.inline("⬅️ Voltar ao Painel", b"back_to_admin")]]
                )

                # Remover arquivo temporário
                os.remove(filepath)

                await log_action(f"Admin {user_id} exportou lista de {total_users} usuários")

            except Exception as e:
                await event.edit(f"❌ **Erro ao gerar arquivo:**\n\n`{str(e)}`", buttons=[[Button.inline("⬅️ Voltar", b"back_to_admin")]])
                logger.error(f"Erro ao exportar usuários: {e}")
            return
        elif data == 'broadcast_panel':
            await event.edit("📢 **Broadcast para Todos os Usuários**\n\nUse o comando:\n`/broadcast <sua mensagem>`\n\nExemplo:\n`/broadcast Olá! Nova atualização disponível.`", buttons=[[Button.inline("⬅️ Voltar", b"back_to_admin")]])

            return

        elif data.startswith('gift_'):
            days = int(data.split('_')[1])
            plan_name = f"{days} dia(s)"
            if days >= 36500: plan_name = "Vitalício ✨"
            token = generate_token(days)
            await event.respond(f'✅ Token de **{plan_name}** gerado:\n\n`{token}`', parse_mode='Markdown')
            return

        elif data == 'back_to_admin':
            await send_start_message(event, admin_view=True)
            return

        elif data == 'stats':
            total_users, banned_users = get_all_users_count(), get_banned_users_count()
            total_logins, total_domains = get_db_stats()
            stats_msg = (f"📊 **Estatísticas**\n\n**Usuários:**\n- Total: `{total_users}` | Banidos: `{banned_users}`\n\n**Banco de Dados:**\n- Logins: `{total_logins:,}`\n- Domínios: `{total_domains:,}`".replace(",", "."))
            await event.edit(stats_msg, parse_mode='Markdown', buttons=[[Button.inline("⬅️ Voltar", b"back_to_admin")]])
            return

        elif data == 'audit':
            text = "**🛡️ Auditoria**\n\n- `/ban <ID>`\n- `/unban <ID>`\n- `/cancelar <ID>`\n- `/autorizar <ID> <tempo>`\n- `/info <ID>`\n- `/reload_admins`"
            await event.edit(text, buttons=[[Button.inline("⬅️ Voltar", b"back_to_admin")]])
            return

        elif data == 'show_admin_commands':
            text = ("**📖 Comandos**\n\n**Usuários:**\n`/ban <ID>`\n`/unban <ID>`\n`/cancelar <ID>`\n`/autorizar <ID> <tempo>`\n`/info <ID>`\n\n**Sistema:**\n`/reload_admins`\n`/stats`\n`/dbinfo`\n`/cache`\n\n**Arquivos:**\nEnvie um `.txt` para adicionar logins.")
            await event.edit(text, buttons=[[Button.inline("⬅️ Voltar", b"back_to_admin")]])
            return

        elif data == 'clear_db_prompt':
            await event.edit("**⚠️ ATENÇÃO!**\nApagar **TODOS OS LOGINS**? Ação irreversível.", buttons=[[Button.inline("🔴 SIM", b"confirm_clear_db"), Button.inline("Cancelar", b"back_to_admin")]])
            return

        elif data == 'confirm_clear_db':
            await event.edit("⏳ Apagando logins...")
            clear_logins_db()
            await event.edit("✅ **Logins Removidos!**", buttons=[[Button.inline("⬅️ Voltar", b"back_to_admin")]])
            return

        elif data == 'active_tokens':
            tokens = get_unused_tokens()
            if not tokens:
                await event.edit("Não há tokens ativos no momento.", buttons=[[Button.inline("⬅️ Voltar", b"back_to_admin")]])
                return
            message = "**🔑 Tokens Ativos (não resgatados):**\n\n"
            for token, days in tokens:
                plan = f"{days}d"
                if days >= 36500: plan = "Vitalício"
                message += f"- `{token}` ({plan})\n"
            await event.edit(message, parse_mode='Markdown', buttons=[[Button.inline("⬅️ Voltar", b"back_to_admin")]])
            return

        elif data == 'broadcast_prompt':
            await event.edit(
                "📢 **Função Broadcast**\n\n"
                "Para enviar uma mensagem para todos os usuários, use:\n\n"
                "`/broadcast <sua mensagem>`\n\n"
                "**Exemplo:**\n"
                "`/broadcast Olá pessoal! Temos novidades incríveis chegando em breve! 🚀`",
                buttons=[[Button.inline("⬅️ Voltar", b"back_to_admin")]],
                parse_mode='Markdown'
            )
            return

        elif data.startswith('confirm_broadcast:'):
            message_id = int(data.split(':')[1])
            if message_id in broadcast_temp_messages:
                broadcast_message = broadcast_temp_messages[message_id]
                await event.edit("📤 **Enviando broadcast...**\n\nPor favor, aguarde.")

                # Enviar broadcast em background
                asyncio.create_task(send_broadcast_to_all(broadcast_message, user_id))

                # Limpar mensagem temporária
                del broadcast_temp_messages[message_id]
            else:
                await event.edit("❌ Mensagem expirada. Tente novamente.")
            return

        elif data == 'cancel_broadcast':
            await event.edit("❌ Broadcast cancelado.", buttons=[[Button.inline("⬅️ Voltar", b"back_to_admin")]])
            return

        elif data == 'clear_cache':
            cache_inteligente.clear()
            await event.edit("✅ **Cache Limpo!**\n\nTodos os dados em cache foram removidos.", buttons=[[Button.inline("⬅️ Voltar", b"back_to_admin")]])
            return

        elif data == 'refresh_cache_stats':
            # Redirecionar para o comando de cache
            await cache_stats_command(event)
            return

        elif data == 'cache_panel':
            await cache_stats_command(event)
            return

        elif data == 'ping_panel':
            # Simular comando de ping para admin
            from types import SimpleNamespace
            fake_event = SimpleNamespace()
            fake_event.respond = event.edit
            fake_event.get_sender = event.get_sender
            await ping_command(fake_event)
            return

        elif data == 'refresh_ping':
            # Atualizar teste de ping
            from types import SimpleNamespace
            fake_event = SimpleNamespace()
            fake_event.respond = event.edit
            fake_event.get_sender = event.get_sender
            await ping_command(fake_event)
            return

    # Verificar se tem acesso (autorizado, admin ou teste)
    has_user_access, access_type = has_access(user_id)
    if has_user_access:
        if data == 'prompt_search':
            await event.respond("🔍 Para buscar, use o comando:\n/search <dominio>\n\nExemplo: /search google.com")
            return

        elif data == 'my_history':
            history = get_user_search_history(user_id, 10)
            if not history:
                await event.edit("📜 𝐒𝐞𝐮 𝐇𝐢𝐬𝐭𝐨́𝐫𝐢𝐜𝐨 𝐝𝐞 𝐁𝐮𝐬𝐜𝐚𝐬\n\n📭 Nenhuma busca realizada ainda.\n\n💡 Use /search <dominio> para fazer sua primeira busca!", buttons=[[Button.inline("⬅️ Voltar", b"back_to_member_start")]])
            else:
                history_text = "📜 𝐒𝐞𝐮 𝐇𝐢𝐬𝐭𝐨́𝐫𝐢𝐜𝐨 𝐝𝐞 𝐁𝐮𝐬𝐜𝐚𝐬\n\n"
                for domain, count, date in history:
                    try:
                        date_obj = datetime.fromisoformat(date.replace("Z", "+00:00"))
                        formatted_date = date_obj.strftime("%d/%m/%Y %H:%M")
                    except:
                        formatted_date = date
                    history_text += f"🔍 {domain}\n📊 {count:,} logins encontrados\n🕒 {formatted_date}\n\n".replace(",", ".")
                await event.edit(history_text, buttons=[[Button.inline("⬅️ Voltar", b"back_to_member_start")]])
            return

        elif data == 'my_access':
            if user_id in ADMIN_IDS:
                expiry_text = "Vitalício ✨"
                status_text = "👑 Administrador"
            elif is_authorized(user_id):
                expiry_text = get_user_expiry_date(user_id)
                status_text = "💎 Membro Premium"
            else:
                # Usuário em teste
                trial_status = get_trial_status(user_id)
                if trial_status["has_trial"]:
                    expiry_text = f"{trial_status['remaining_minutes']} minutos"
                    status_text = "🆓 Teste Gratuito"
                else:
                    expiry_text = "Expirado"
                    status_text = "❌ Sem Acesso"
            
            access_details = (
                f"ℹ️ **Detalhes do Seu Acesso**\n\n"
                f"🏷️ **Status:** {status_text}\n"
                f"📅 **Expira em:** {expiry_text}\n\n"
            )
            
            if access_type == "trial":
                access_details += (
                    "🆓 **Período de Teste Ativo**\n"
                    "• Acesso completo a todas as funcionalidades\n"
                    "• Aproveite para testar o sistema\n\n"
                    "💎 **Gostou? Adquira um plano premium:**\n"
                    "• Acesso ilimitado\n"
                    "• Suporte prioritário\n"
                    "• Sem limitações de tempo\n\n"
                )
            
            access_details += "🤖 @Olhosdecristo_bot"
            
            buttons = []
            if access_type == "trial":
                buttons.append([Button.inline("💎 Ver Planos Premium", b"group_plans")])
            buttons.append([Button.inline("⬅️ Voltar", b"back_to_member_start")])
            
            await event.edit(access_details, buttons=buttons)
            return

        

        elif data == 'help_member':
            help_text = "❓ 𝐀𝐣𝐮𝐝𝐚\n\n🔍 /search <dominio> - Buscar logins\n💼 /afiliado - Painel de afiliado\n🏠 /start - Menu principal\n🔄 /reset - Resetar dados"
            await event.edit(help_text, buttons=[[Button.inline("⬅️ Voltar", b"back_to_member_start")]])
            return

        elif data == 'affiliate_panel':
            await affiliate_command(event)
            return

        elif data == 'withdraw_prompt':
            stats = get_affiliate_stats(user_id)
            if stats['earnings'] > 0:
                request_withdrawal(user_id, stats['earnings'])
                await event.edit(f"✅ 𝐒𝐨𝐥𝐢𝐜𝐢𝐭𝐚𝐜̧𝐚̃𝐨 𝐝𝐞 𝐒𝐚𝐪𝐮𝐞 𝐄𝐧𝐯𝐢𝐚𝐝𝐚!\n\nSua solicitação para sacar R$ {stats['earnings']:.2f} foi enviada ao administrador.", buttons=[[Button.inline("⬅️ Voltar", b"affiliate_panel_back")]])
            else: 
                await event.answer("Você não tem saldo para sacar.", alert=True)
            return

        elif data == 'top_affiliates':
            await top_affiliates_command(event)
            return

        elif data == 'back_to_member_start':
            if user_id in ADMIN_IDS:
                await send_start_message(event, admin_view=False)
            else:
                await send_start_message(event)
            return

        elif data == 'affiliate_panel_back':
            await affiliate_command(event)
            return

    # ACESSO LIBERADO PARA TODOS - SEM RESTRIÇÕES
    pass

@client.on(events.CallbackQuery(pattern=r'^deletefile:(\d+)$'))
async def delete_file_handler(event):
    try:
        id_user_btn = int(event.pattern_match.group(1))
        if event.sender_id != id_user_btn:
            await event.answer("APENAS O USUÁRIO QUE RECEBEU O ARQUIVO PODE APAGAR.\n\nERR_USER_NOT_VERIFIED", alert=True)
            return
        await event.delete()
    except Exception as e:
        logger.error(f"Erro no delete_file_handler: {e}")

# --- 7. INICIALIZAÇÃO ---

def reload_admins():
    global ADMIN_IDS
    ADMIN_IDS = get_admins()
    print(f"📋 [ADMIN] Carregados {len(ADMIN_IDS)} administradores: {ADMIN_IDS}")

async def main():
    global ADMIN_IDS
    print("🚀 [INFO] Iniciando o bot Olhosdecristo_bot...")

    try:
        # Inicializar sistema de saúde
        bot_health["start_time"] = datetime.now(SAO_PAULO_TZ)
        update_bot_health("startup")

        # Inicializar banco de dados
        init_db()
        print("✅ [INFO] Banco de dados inicializado.")

        # Iniciar servidor web
        keep_alive()
        print("✅ [INFO] Servidor web iniciado.")

        # Carregar administradores
        reload_admins()
        print(f"✅ [INFO] {len(ADMIN_IDS)} administradores carregados.")

        # Configurar scheduler
        scheduler.add_job(check_expirations, 'cron', hour=10, minute=0)
        scheduler.add_job(save_cache_periodically, 'interval', minutes=10)  # Salvar cache a cada 10 minutos
        scheduler.add_job(cleanup_cache_periodically, 'interval', hours=2)  # Limpar cache expirado a cada 2 horas
        scheduler.start()
        print("⏰ [SCHEDULER] Agendador de tarefas iniciado.")

        # Conectar ao Telegram
        print("🔐 [INFO] Conectando ao Telegram...")
        await client.start(bot_token=BOT_TOKEN)
        print("✅ [INFO] Bot conectado ao Telegram com sucesso!")

        # Verificar informações do bot
        me = await client.get_me()
        print(f"🤖 [INFO] Bot @{me.username} ({me.first_name}) está online!")

        # Recarregar admins após conexão e enviar notificação
        reload_admins()
        await log_action(f"**Bot `{me.first_name}` ficou online!** - Admins carregados: {len(ADMIN_IDS)}")

        # Enviar notificação para o admin principal se possível
        try:
            if MEU_ID in ADMIN_IDS:
                await client.send_message(MEU_ID, "🚀 **Bot Online!**\n\nO bot foi iniciado com sucesso e está pronto para uso.")
        except Exception as e:
            print(f"⚠️ [WARNING] Não foi possível enviar notificação para admin: {e}")

        print("🎉 [INFO] Inicialização completa! Bot em funcionamento.")
        print("📱 [INFO] Otimizado para dispositivos móveis potentes (S24 Ultra e similares)")
        print(f"🧠 [CACHE] Cache configurado: {CACHE_MAX_SIZE} domínios por {CACHE_TTL_HOURS}h")
        print(f"⚡ [PERFORMANCE] Chunks otimizados: {MOBILE_CHUNK_SIZE:,} linhas")

        # Manter bot rodando
        await client.run_until_disconnected()

    except Exception as e:
        print(f"❌ [ERROR] Erro crítico durante inicialização: {e}")
        logger.error(f"Erro crítico durante inicialização: {e}")
        raise
    finally:
        print("🔄 [INFO] Bot desconectado.")

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
