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
    BOT_TOKEN = os.environ.get("BOT_TOKEN", "7369466703:AAHALdZSqvCVkfyhs6sW-JoHkrnX0r9e7Rw") # Usando .get para valor padrão
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


# Instância global do cache
cache_inteligente = CacheInteligente(max_size=150, ttl_hours=12)

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
                username TEXT
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

def search_db(domain: str, limit: int = 15000) -> list:
    # Primeiro, tentar buscar no cache
    cached_results = cache_inteligente.get(domain)
    if cached_results is not None:
        # Retornar TODOS os resultados do cache, sem limitação
        print(f"[CACHE HIT] {domain} - {len(cached_results)} resultados do cache (sem limitação)")
        return cached_results

    # Cache miss - buscar no banco de dados
    search_term = domain.lower()
    subdomain_pattern = f"%.{search_term}"

    results = []
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
            LIMIT ?
        """
        params = (search_term, subdomain_pattern, search_term, subdomain_pattern, limit)
        cursor.execute(query, params)
        results = [row[0] for row in cursor.fetchall()]

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
    if user_id in ADMIN_IDS: return True
    if is_banned(user_id): return False
    with sqlite3.connect(DB_FILE) as conn:
        cur = conn.cursor()
        cur.execute("SELECT expiry_date FROM authorizations WHERE user_id = ?", (user_id,))
        result = cur.fetchone()
        if not result: return False
        try:
            expiry_date = datetime.fromisoformat(result[0])
            if expiry_date > datetime.now(SAO_PAULO_TZ) + timedelta(days=365*90): return True
            return datetime.now(SAO_PAULO_TZ) < expiry_date
        except: return False

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
    # Fallback: classe simples caso o import falhe
    class LoginSearch:
        def __init__(self, url: str, user_id: int, pasta_temp: str, cancel_flag: Dict, contador_callback=None):
            self.url = url
            self.user_id = user_id
            self.pasta_temp = pasta_temp
            self.cancel_flag = cancel_flag
            self.contador_callback = contador_callback

        def buscar(self):
            # Fallback simples - buscar apenas no banco local
            results = search_db(self.url)
            arquivo_raw = os.path.join(self.pasta_temp, f"{self.user_id}.txt")
            arquivo_formatado = os.path.join(self.pasta_temp, f"{self.user_id}_formatado.txt")

            with open(arquivo_raw, 'w', encoding='utf-8') as f:
                for result in results:
                    f.write(result + '\n')

            with open(arquivo_formatado, 'w', encoding='utf-8') as f:
                for linha in results:
                    if ':' in linha:
                        partes = linha.split(':', 1)
                        email, senha = partes[0].strip(), partes[1].strip()
                        f.write(f"\u2022 EMAIL: {email}\n\u2022 SENHA: {senha}\n\n")

            return arquivo_raw, arquivo_formatado



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
            [Button.inline("🛡️ Auditoria", b"audit"), Button.inline("👥 Export Users", b"export_users")],
            [Button.inline("🗑️ Limpar DB", b"clear_db_prompt"), Button.inline("📖 Ver Comandos", b"show_admin_commands")],
            [Button.inline("👤 Modo Membro", b"back_to_member_start")]
        ]
        message = f"⚙️ 𝗣𝗮𝗶𝗻𝗲𝗹 𝗱𝗲 𝗔𝗱𝗺𝗶𝗻𝗶𝘀𝘁𝗿𝗮𝗰̧𝗮̃𝗼\n\n👋 Olá, {user.first_name}!\n🆔 Seu ID: {user.id}\n👑 Seu plano: Administrador\n\n📋 Selecione uma opção:\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n💻 By: Tequ1la"
        if is_callback:
            await event_or_user.edit(message, buttons=admin_buttons)
        else:
            await respond_method(message, buttons=admin_buttons)
    elif is_authorized(user.id):
        expiry_date_str = get_user_expiry_date(user.id)
        member_buttons = [
            [Button.inline("🔍 Nova Busca", b"prompt_search"), Button.inline("📜 Histórico Buscas", b"my_history")],
            [Button.inline("💎 Planos para Grupos", b"group_plans"), Button.inline("💼 Painel de Afiliado", b"affiliate_panel")],
            [Button.inline("ℹ️ Detalhes do Acesso", b"my_access"), Button.inline("❓ Ajuda", b"help_member")],
            [Button.url("💬 Suporte", "https://t.me/Tequ1ladoxxado")]
        ]
        message = (
            f"🎉 𝗕𝗲𝗺-𝘃𝗶𝗻𝗱𝗼(𝗮) 𝗱𝗲 𝘃𝗼𝗹𝘁𝗮, {user.first_name}!\n\n"
            f"✨ Bem-vindo ao sistema mais avançado de consultas!\n\n"
            f"🆔 Seu ID: {user.id}\n"
            f"📅 Seu plano: Ativo até {expiry_date_str}\n\n"
            "📱 Use os botões abaixo para continuar:\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "💻 By: Tequ1la"
        )
        if is_callback:
            await event_or_user.edit(message, buttons=member_buttons)
        else:
            await respond_method(message, buttons=member_buttons)
    else:
        new_user_buttons = [
            [Button.url("✅ Adquirir Plano Individual", "https://t.me/Tequ1ladoxxado"), Button.inline("💎 Planos para Grupos", b"group_plans")],
            [Button.inline("🚀 Já tenho um token", b"redeem_token_prompt"), Button.url("💬 Suporte", "https://t.me/Tequ1ladoxxado")]
        ]
        message = (
            f"👋 𝗢𝗹𝗮́, {user.first_name}, 𝗕𝗲𝗺-𝘃𝗶𝗻𝗱𝗼(𝗮) 𝗮𝗼 𝗢𝗹𝗵𝗼𝘀𝗱𝗲𝗰𝗿𝗶𝘀𝘁𝗼_𝗯𝗼𝘁!\n\n"
            "🚀 A plataforma mais avançada para consultas de Logins!\n"
            "⚡ Busca instantânea com cache inteligente\n"
            "🎯 Resultados precisos e atualizados\n\n"
            f"✅ 𝗦𝗲𝘂 𝗣𝗲𝗿𝗳𝗶𝗹\n"
            f"🆔 ID: {user.id}\n"
            f"📊 Status: Sem plano ativo\n\n"
            "📢 Adquira um plano para começar a usar nossa tecnologia!\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "💻 By: Tequ1la"
        )
        if is_callback:
            await event_or_user.edit(message, buttons=new_user_buttons)
        else:
            await respond_method(message, buttons=new_user_buttons)

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
    if not is_authorized(event.sender_id):
        await event.respond("✋ Você precisa ter um plano ativo para acessar o painel de afiliado.")
        return
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
    if event.sender_id not in ADMIN_IDS:
        await event.respond("❌ Comando disponível apenas para administradores.")
        return

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
    if event.sender_id not in ADMIN_IDS:
        await event.respond("❌ Comando disponível apenas para administradores.")
        return

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
    if event.sender_id not in ADMIN_IDS:
        await event.respond("❌ Comando disponível apenas para administradores.")
        return

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
    if event.sender_id not in ADMIN_IDS:
        await event.respond("❌ Comando disponível apenas para administradores.")
        return
    
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

@client.on(events.NewMessage(pattern=r'/bulk_add'))
async def bulk_add_prompt(event):
    if event.sender_id not in ADMIN_IDS:
        await event.respond("❌ Comando disponível apenas para administradores.")
        return
    
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
    if event.sender_id not in ADMIN_IDS:
        await event.respond("❌ Comando disponível apenas para administradores.")
        return

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

@client.on(events.NewMessage(pattern=r'/cache'))
async def cache_stats_command(event):
    if event.sender_id not in ADMIN_IDS:
        await event.respond("❌ Comando disponível apenas para administradores.")
        return

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
            "✅ 𝗦𝗲𝘂𝘀 𝗱𝗮𝗱𝗼𝘀 𝗳𝗼𝗿𝗮𝗺 𝗿𝗲𝘀𝗲𝘁𝗮𝗱𝗼𝘀!\n\n🔄 Agora você pode utilizar os comandos novamente.\n\n🤖 @Olhosdecristo_bot",
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
            "📝 **Formato correto:**\n"
            "`/search <dominio>`\n\n"
            "✅ **Exemplos válidos:**\n"
            "• `/search google.com`\n"
            "• `/search facebook.com`\n"
            "• `/search instagram.com`\n"
            "• `/search github.com`\n\n"
            "⚠️ **Dicas importantes:**\n"
            "• Use apenas o domínio (sem http/https)\n"
            "• Não use espaços no domínio\n"
            "• Aguarde o resultado da busca antes de fazer outra\n\n"
            "💡 **Exemplo de uso:**\n"
            "`/search roblox.com`\n\n"
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



@client.on(events.NewMessage(pattern=r'^/search (.+)$'))
async def search_handler(event):
    try:
        termo = event.pattern_match.group(1)
        sender = await event.get_sender()
        id_user = sender.id

        if not termo_valido(termo):
            return await event.reply(
                "❌ 𝗨𝗥𝗟 𝗶𝗻𝘃𝗮́𝗹𝗶𝗱𝗮 𝗼𝘂 𝗻𝗮̃𝗼 𝗶𝗻𝗳𝗼𝗿𝗺𝗮𝗱𝗮\n\n💡 Exemplo: /search google.com\n\n🤖 @Olhosdecristo_bot",
                buttons=[[Button.inline("❌ Apagar Mensagem", data=f"apagarmensagem:{id_user}")]]
            )

        if id_user in usuarios_bloqueados:
            return await event.reply(
                "⛔ 𝗔𝗴𝘂𝗮𝗿𝗱𝗲 𝗮𝘁𝗲́ 𝗾𝘂𝗲 𝗮 𝗽𝗲𝘀𝗾𝘂𝗶𝘀𝗮 𝘀𝗲𝗷𝗮 𝗳𝗲𝗶𝘁𝗮!\n\n💡 Use o comando /reset para resetar suas informações\n\n🤖 @Olhosdecristo_bot",
                buttons=[[Button.inline("❌ Apagar Mensagem", data=f"apagarmensagem:{id_user}")]]
            )

        url = termo.strip()
        usuarios_bloqueados.add(id_user)

        nome = f"{getattr(sender, 'first_name', '')} {getattr(sender, 'last_name', '')}".strip()
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        hash_nome = str(id_user)

        usuarios_autorizados[id_user] = hash_nome
        mensagens_origem[id_user] = event.id
        urls_busca[id_user] = url
        tasks_canceladas[hash_nome] = {'cancelled': False}

        pasta_temp = os.path.join(TEMP_DIR, str(id_user))
        os.makedirs(pasta_temp, exist_ok=True)

        # Verificar se está no cache antes de mostrar mensagem de busca
        cached_check = cache_inteligente.get(url)
        if cached_check is not None:
            initial_text = f"⚡ 𝗖𝗮𝗰𝗵𝗲 𝗛𝗶𝘁! 𝗥𝗲𝘀𝘂𝗹𝘁𝗮𝗱𝗼 𝗶𝗻𝘀𝘁𝗮𝗻𝘁𝗮̂𝗻𝗲𝗼...\n\n🔍 Logins encontrados: {len(cached_check):,}\n\n✨ Dados do cache inteligente\n\n🤖 @Olhosdecristo_bot".replace(",", ".")
        else:
            initial_text = "☁️ 𝗣𝗿𝗼𝗰𝘂𝗿𝗮𝗻𝗱𝗼 𝗱𝗮𝗱𝗼𝘀 𝗱𝗮 𝗨𝗥𝗟 𝗳𝗼𝗿𝗻𝗲𝗰𝗶𝗱𝗮...\n\n🔍 Logins encontrados: 0\n\n🤖 @Olhosdecristo_bot"

        msg_busca = await client.send_message(
            event.chat_id,
            initial_text,
            buttons=[
                [Button.inline("🚫 Parar Pesquisa", data=f"cancelarbusca:{id_user}")],
                [Button.inline("❌ Apagar Mensagem", data=f"apagarmensagem:{id_user}")]
            ],
            reply_to=event.id
        )

        contador_atual = 0
        lock = asyncio.Lock()

        def contador_callback(novo_contador):
            nonlocal contador_atual
            contador_atual = novo_contador

        async def editar_mensagem_periodicamente():
            while not tasks_canceladas[hash_nome]['cancelled']:
                await asyncio.sleep(5)  # Atualizar a cada 5 segundos (menos overhead)
                async with lock:
                    try:
                        # Verificar se é cache hit
                        if cached_check is not None:
                            new_text = f"⚡ 𝗖𝗮𝗰𝗵𝗲 𝗛𝗶𝘁! 𝗥𝗲𝘀𝘂𝗹𝘁𝗮𝗱𝗼 𝗶𝗻𝘀𝘁𝗮𝗻𝘁𝗮̂𝗻𝗲𝗼...\n\n✨✨✨✨✨✨✨✨✨✨\n\n🔍 Logins encontrados: {contador_atual:,}\n\n⚡ Cache inteligente ativo!\n\n🤖 @Olhosdecristo_bot".replace(",", ".")
                        else:
                            # Criar uma barra de progresso visual
                            if contador_atual > 0:
                                # Estimar progresso baseado na velocidade
                                progress_dots = "⚡" * min(10, (contador_atual // 100) % 10 + 1)
                                new_text = f"☁️ 𝗣𝗿𝗼𝗰𝘂𝗿𝗮𝗻𝗱𝗼 𝗱𝗮𝗱𝗼𝘀 𝗱𝗮 𝗨𝗥𝗟 𝗳𝗼𝗿𝗻𝗲𝗰𝗶𝗱𝗮...\n\n{progress_dots}\n\n🔍 Logins encontrados: {contador_atual:,}\n\n⚡ Buscando em tempo real...\n\n🤖 @Olhosdecristo_bot".replace(",", ".")
                            else:
                                new_text = f"☁️ 𝗣𝗿𝗼𝗰𝘂𝗿𝗮𝗻𝗱𝗼 𝗱𝗮𝗱𝗼𝘀 𝗱𝗮 𝗨𝗥𝗟 𝗳𝗼𝗿𝗻𝗲𝗰𝗶𝗱𝗮...\n\n⏳ Iniciando busca...\n\n🔍 Logins encontrados: {contador_atual}\n\n🤖 @Olhosdecristo_bot"

                        await msg_busca.edit(
                            new_text,
                            buttons=[
                                [Button.inline("🚫 | PARAR PESQUISA", data=f"cancelarbusca:{id_user}")],
                                [Button.inline("❌ | APAGAR MENSAGEM", data=f"apagarmensagem:{id_user}")]
                            ]
                        )
                        print(f"[SEARCH PROGRESS] {contador_atual} logins encontrados para {url}")
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

                    # Criar arquivo raw
                    with open(arquivo_raw, 'w', encoding='utf-8') as f:
                        for result in cached_results:
                            f.write(result + '\n')

                    # Criar arquivo formatado
                    with open(arquivo_formatado, 'w', encoding='utf-8') as f:
                        for linha in cached_results:
                            if ':' in linha:
                                partes = linha.split(':', 1)
                                email, senha = partes[0].strip(), partes[1].strip()
                                f.write(f"\u2022 EMAIL: {email}\n\u2022 SENHA: {senha}\n\n")

                    print(f"[CACHE HIT] {url} - {len(cached_results)} resultados retornados do cache!")
                    return arquivo_raw, arquivo_formatado

                # Cache MISS - buscar na API externa E no banco local
                print(f"[CACHE MISS] {url} - Buscando na API externa e banco local...")

                # Buscar no banco local primeiro (mais rápido) - mas SEM usar cache para evitar recursão
                search_term = url.lower()
                subdomain_pattern = f"%.{search_term}"
                db_results = []

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

                # Buscar na API externa com limite de 50k resultados
                search_instance = LoginSearch(url, id_user, pasta_temp, tasks_canceladas[hash_nome], contador_callback, limite_max=50000)
                arquivo_raw, arquivo_formatado = search_instance.buscar()

                # Ler resultados da API externa
                api_results = []
                if os.path.exists(arquivo_raw):
                    with open(arquivo_raw, 'r', encoding='utf-8') as f:
                        api_results = [linha.strip() for linha in f if linha.strip()]

                # Combinar todos os resultados (API + Banco Local)
                all_results = list(api_results)  # Começar com API externa

                # Adicionar resultados do banco local que não estão na API
                for db_result in db_results:
                    if db_result not in all_results:
                        all_results.append(db_result)

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
                search_completed = not tasks_canceladas[hash_nome]['cancelled']
                if all_results and search_completed:
                    cache_inteligente.set(url, all_results, search_completed=True)
                    print(f"[CACHE SET] {url} - {len(all_results)} resultados combinados adicionados ao cache")
                elif not search_completed:
                    print(f"[CACHE SKIP] {url} - Busca cancelada, não adicionando ao cache")

                print(f"[COMBINED SEARCH] {url} - {len(api_results)} da API + {len(db_results)} do banco = {len(all_results)} total!")
                return arquivo_raw, arquivo_formatado

            except Exception as e:
                logger.error(f"Erro na busca: {e}")
                # Retornar arquivos vazios em caso de erro
                arquivo_raw = os.path.join(pasta_temp, f"{id_user}.txt")
                arquivo_formatado = os.path.join(pasta_temp, f"{id_user}_formatado.txt")

                # Criar arquivos vazios
                with open(arquivo_raw, 'w', encoding='utf-8') as f:
                    pass
                with open(arquivo_formatado, 'w', encoding='utf-8') as f:
                    pass

                return arquivo_raw, arquivo_formatado

        arquivo_raw, arquivo_formatado = await asyncio.to_thread(buscar_wrapper)

        tarefa_editar.cancel()
        try:
            await tarefa_editar
        except asyncio.CancelledError:
            pass

        qtd_logins = 0
        if os.path.exists(arquivo_raw):
            with open(arquivo_raw, "r", encoding="utf-8") as f:
                qtd_logins = sum(1 for _ in f)

        if qtd_logins == 0:
            await msg_busca.edit("❌ 𝗡𝗲𝗻𝗵𝘂𝗺 𝗿𝗲𝘀𝘂𝗹𝘁𝗮𝗱𝗼 𝗳𝗼𝗶 𝗲𝗻𝗰𝗼𝗻𝘁𝗿𝗮𝗱𝗼!\n\n📝 Tente com outro domínio\n\n🤖 @Olhosdecristo_bot")
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

        await client.send_message(
            event.chat_id,
            f"✅ 𝗕𝘂𝘀𝗰𝗮 𝗖𝗼𝗻𝗰𝗹𝘂í𝗱𝗮!\n\n"
            f"🎯 Resultados encontrados: {qtd_logins:,}\n"
            f"🌐 Domínio: {url}\n\n"
            f"📋 Escolha o formato de download:\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💻 By: Tequ1la | @Olhosdecristo_bot".replace(",", "."),
            buttons=[
                [Button.inline("📝 USER:PASS", data=f"format1:{id_user}"),
                 Button.inline("📋 FORMATADO", data=f"format2:{id_user}")],
                [Button.inline("❌ CANCELAR", data=f"cancel:{id_user}")]
            ],
            reply_to=event.id
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

@client.on(events.NewMessage(func=lambda e: e.file and e.sender_id in ADMIN_IDS))
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

    # Sistema de chunk SUPER otimizado baseado no tamanho
    if file_size > 500 * 1024 * 1024:  # > 500MB
        CHUNK_SIZE = 200000  # Chunks gigantes para arquivos enormes
        UPDATE_FREQUENCY = 50000  # Atualizar menos para performance máxima
    elif file_size > 100 * 1024 * 1024:  # > 100MB
        CHUNK_SIZE = 150000  # Chunks grandes
        UPDATE_FREQUENCY = 25000
    elif file_size > 10 * 1024 * 1024:  # > 10MB
        CHUNK_SIZE = 100000  # Chunks médios
        UPDATE_FREQUENCY = 10000
    else:
        CHUNK_SIZE = 50000  # Chunk padrão
        UPDATE_FREQUENCY = 5000

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
    if event.sender_id not in ADMIN_IDS: return

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
        nome_arquivo = f"{id_user_btn}.txt" if acao == "format1" else f"{id_user_btn}_formatado.txt"
        caminho = os.path.join(pasta, nome_arquivo)

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

        await client.send_file(
            event.chat_id,
            file=caminho,
            caption=caption,
            buttons=[[Button.inline("❌ Apagar Mensagem", data=f"deletefile:{id_user_btn}")]],
            reply_to=mensagens_origem.get(id_user_btn)
        )

        try:
            await client.send_message(MEU_ID, f"""**⚠️ | NOVA CONSULTA DE LOGIN**\n\n**• QUEM FOI:** {mention}\n**• URL:** {urls_busca.get(id_user_btn, "desconhecida")}\n**• QUANTIDADE:** {qtd}\n\n🤖 @Olhosdecristo_bot""")
        except Exception as e:
            logger.error(f"Erro ao notificar admin: {e}")

        shutil.rmtree(pasta, ignore_errors=True)
        return

    # Handlers para Admins
    if user_id in ADMIN_IDS:
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

    # Handlers para Membros Autorizados
    if is_authorized(user_id):
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
            else:
                expiry_text = get_user_expiry_date(user_id)
                status_text = "💎 Membro Premium"
            await event.edit(f"✅ 𝐒𝐞𝐮 𝐚𝐜𝐞𝐬𝐬𝐨 𝐞𝐬𝐭𝐚́ 𝐚𝐭𝐢𝐯𝐨!\n\n📅 Expira em: {expiry_text}\n🏷️ Status: {status_text}", buttons=[[Button.inline("⬅️ Voltar", b"back_to_member_start")]])
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

    # Acesso negado para usuários não autorizados
    await event.answer("🚫 Acesso restrito.", alert=True)

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
