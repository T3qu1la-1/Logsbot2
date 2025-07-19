import os
import json
import re
import requests
import time
import sqlite3
from typing import Dict, Callable, Optional, List
import logging

# Configurar logging específico
logger = logging.getLogger(__name__)

class LoginSearch:
    def __init__(self, url: str, id_user: int, pasta_temp: str, cancel_flag: Dict, contador_callback: Optional[Callable] = None, limite_max: Optional[int] = 80000, search_term: Optional[str] = None, disable_pause: bool = False):
        self.url = url
        self.id_user = id_user
        self.pasta_temp = pasta_temp
        self.cancel_flag = cancel_flag
        self.contador_callback = contador_callback
        self.limite_max = limite_max
        self.search_term = search_term.lower() if search_term else None
        self.pause_intervals = 20000
        self.should_pause = False
        self.disable_pause = disable_pause
        self.is_extension_search = url.startswith('*.')
        os.makedirs(self.pasta_temp, exist_ok=True)
        self.start_time = None
        self.end_time = None
        self.last_activity = None

    def buscar(self):
        """Busca logins combinando API externa + banco local"""
        raw_path = os.path.join(self.pasta_temp, f"{self.id_user}.txt")
        formatado_path = os.path.join(self.pasta_temp, f"{self.id_user}_formatado.txt")

        contador = 0
        regex_valido = re.compile(r'^[a-zA-Z0-9!@#$%^&*()\-_=+\[\]{}|;:\'\",.<>/?`~\\]+$')

        max_retries = 3
        retry_count = 0

        self.start_time = time.time()
        self.last_activity = self.start_time

        print(f"[COMBINED SEARCH] 🚀 Iniciando busca COMBINADA para {self.url}")
        print(f"[COMBINED SEARCH] ⏰ Início: {time.strftime('%H:%M:%S', time.localtime(self.start_time))}")

        # Preparar arquivos com créditos
        try:
            # Usar nome da URL para os arquivos
            url_clean = re.sub(r'[^\w\-_\.]', '_', self.url)
            if url_clean.startswith('_'):
                url_clean = url_clean[1:]
            if url_clean.endswith('_'):
                url_clean = url_clean[:-1]
            
            raw_path = os.path.join(self.pasta_temp, f"{url_clean}_logins.txt")
            formatado_path = os.path.join(self.pasta_temp, f"{url_clean}_formatado.txt")

            with open(raw_path, "w", encoding="utf-8") as f:
                f.write(f"# =====================================\n")
                f.write(f"# 🤖 Bot: Olhos de Cristo Bot\n")
                f.write(f"# 📱 Telegram: @Olhosdecristo_bot\n")
                f.write(f"# 🌐 Domínio: {self.url}\n")
                f.write(f"# ⏰ Data: {time.strftime('%d/%m/%Y %H:%M:%S')}\n")
                f.write(f"# =====================================\n\n")

            with open(formatado_path, "w", encoding="utf-8") as f:
                f.write(f"{'='*80}\n")
                f.write(f"{'🤖 OLHOS DE CRISTO BOT - RESULTADOS DE BUSCA 🤖':^80}\n")
                f.write(f"{'='*80}\n")
                f.write(f"📱 Telegram: @Olhosdecristo_bot\n")
                f.write(f"🌐 Domínio Pesquisado: {self.url}\n")
                f.write(f"⏰ Data da Busca: {time.strftime('%d/%m/%Y %H:%M:%S')}\n")
                f.write(f"🎯 Desenvolvido por: @Tequ1ladoxxado\n")
                f.write(f"✨ Bot Premium de Buscas Privadas\n")
                f.write(f"{'='*80}\n\n")
                f.write(f"📊 RESULTADOS ENCONTRADOS:\n\n")
        except Exception as e:
            print(f"[COMBINED SEARCH] ❌ Erro ao criar arquivos: {e}")
            return raw_path, formatado_path

        # ETAPA 1: Buscar no banco de dados local primeiro
        db_results = self._buscar_banco_local()
        print(f"[COMBINED SEARCH] 📊 Banco local: {len(db_results)} logins encontrados")

        # Adicionar resultados do banco local aos arquivos
        contador += self._adicionar_resultados_arquivos(db_results, raw_path, formatado_path, "BANCO LOCAL")

        # Atualizar callback com resultados do banco local
        if self.contador_callback:
            try:
                self.contador_callback(contador)
            except Exception as e:
                print(f"[COMBINED SEARCH] ⚠️ Erro no callback: {e}")

        # ETAPA 2: Buscar na API externa
        print(f"[COMBINED SEARCH] 🌐 Iniciando busca na API externa...")
        contador = self._buscar_api_externa(raw_path, formatado_path, contador)

        # Registrar tempo final
        if self.end_time is None:
            self.end_time = time.time()
            duration = self.end_time - self.start_time
            print(f"[COMBINED SEARCH] ⏰ Finalizada: {time.strftime('%H:%M:%S', time.localtime(self.end_time))}")
            print(f"[COMBINED SEARCH] ⏱️ Duração: {duration:.2f}s ({duration/60:.1f}min)")
            if contador > 0:
                print(f"[COMBINED SEARCH] 📈 Performance: {contador/duration:.1f} logins/seg")
                print(f"[COMBINED SEARCH] 🎯 Total COMBINADO: {contador} logins (Banco Local + API Externa)")

        # Verificar se encontrou resultados
        if contador == 0:
            print(f"[COMBINED SEARCH] ⚠️ Nenhum resultado encontrado para {self.url}")

        return raw_path, formatado_path


    def _processar_stream(self, response, raw_path, formatado_path, regex_valido, contador):
        """Processa o stream SSE de forma robusta"""
        buffer = ""
        inactivity_timeout = 60  # 60 segundos sem dados
        chunk_size = 4096  # Chunks menores para melhor controle

        print(f"[API SEARCH] 📊 Processando stream...")

        try:
            with open(raw_path, "w", encoding="utf-8") as f_raw, \
                 open(formatado_path, "w", encoding="utf-8") as f_fmt:

                for chunk in response.iter_content(chunk_size=chunk_size, decode_unicode=True):
                    # Verificar cancelamento
                    if self.cancel_flag.get('cancelled', False):
                        print(f"[API SEARCH] 🛑 Busca cancelada pelo usuário")
                        break

                    if chunk:
                        buffer += chunk
                        self.last_activity = time.time()

                        # Processar linhas completas
                        while '\n' in buffer:
                            line, buffer = buffer.split('\n', 1)
                            line = line.strip()

                            if line and line.startswith('data: '):
                                event_data = line[6:].strip()

                                if self._processar_evento(event_data, f_raw, f_fmt, regex_valido):
                                    contador += 1

                                    # Callback para interface
                                    if self.contador_callback and (contador % 10 == 0 or contador < 100):
                                        try:
                                            self.contador_callback(contador)
                                        except Exception as e:
                                            print(f"[API SEARCH] ⚠️ Erro no callback: {e}")

                                    # Log de progresso
                                    if contador % 1000 == 0:
                                        elapsed = time.time() - self.start_time
                                        speed = contador / elapsed if elapsed > 0 else 0
                                        print(f"[API SEARCH] 📊 {contador:,} logins em {elapsed:.1f}s ({speed:.1f}/s)")

                                    # Verificar pausa automática nos 20k logins
                                    if not self.disable_pause and contador > 0 and contador % self.pause_intervals == 0:
                                        print(f"[API SEARCH] ⏸️ Pausa automática aos {contador:,} logins")
                                        self.cancel_flag['pause_at'] = contador
                                        # Finalizar arquivos
                                        f_raw.flush()
                                        f_fmt.flush()
                                        return contador

                                    # Verificar limite máximo
                                    if self.limite_max and contador >= self.limite_max:
                                        print(f"[API SEARCH] 🎯 Limite de {self.limite_max:,} logins atingido")
                                        return contador

                    else:
                        # Verificar timeout de inatividade
                        if time.time() - self.last_activity > inactivity_timeout:
                            print(f"[API SEARCH] ⏰ Timeout de inatividade ({inactivity_timeout}s)")
                            break

                # Processar buffer final
                if buffer.strip():
                    line = buffer.strip()
                    if line.startswith('data: '):
                        event_data = line[6:].strip()
                        if self._processar_evento(event_data, f_raw, f_fmt, regex_valido):
                            contador += 1

        except Exception as e:
            print(f"[API SEARCH] ❌ Erro ao processar stream: {e}")
            logger.error(f"Erro ao processar stream: {e}")

        return contador

    def _processar_evento(self, event_data: str, f_raw, f_fmt, regex_valido) -> bool:
        """Processa um evento SSE de forma robusta"""
        try:
            if not event_data or event_data.lower() in ['', 'null', 'undefined', 'none']:
                return False

            user_limpo = ""
            passwd_limpo = ""
            url_ = self.url

            # Tentar processar como JSON
            try:
                data = json.loads(event_data)
                if isinstance(data, dict):
                    url_ = data.get("url", self.url)
                    user = data.get("user", "")
                    passwd = data.get("pass", "")

                    if user and passwd and user.upper() not in ["EMPTY", "NULL", "UNDEFINED"]:
                        user_limpo = self._limpar_texto(user, regex_valido)
                        passwd_limpo = self._limpar_texto(passwd, regex_valido)

            except (json.JSONDecodeError, TypeError):
                # Processar como texto simples formato user:pass
                if ':' in event_data:
                    parts = event_data.split(':', 1)
                    if len(parts) == 2:
                        user_limpo = self._limpar_texto(parts[0], regex_valido)
                        passwd_limpo = self._limpar_texto(parts[1], regex_valido)

            # Validar dados
            if not user_limpo or not passwd_limpo:
                return False

            # Verificar se é email válido
            if '@' not in user_limpo:
                return False

            # Filtro inteligente por termo
            if self.search_term:
                search_content = f"{user_limpo.lower()} {passwd_limpo.lower()} {url_.lower()}"
                if self.search_term not in search_content:
                    return False

            # Escrever nos arquivos
            f_raw.write(f"{user_limpo}:{passwd_limpo}\n")
            f_fmt.write(f"🔹 URL: {url_}\n")
            f_fmt.write(f"📧 EMAIL: {user_limpo}\n")
            f_fmt.write(f"🔐 SENHA: {passwd_limpo}\n")
            f_fmt.write(f"📍 FONTE: API EXTERNA\n")
            f_fmt.write(f"{'-'*50}\n\n")
            return True

        except Exception as e:
            print(f"[API SEARCH] ⚠️ Erro ao processar evento: {e}")
            return False

    def _limpar_texto(self, texto: str, regex_valido) -> str:
        """Limpa e valida texto removendo caracteres inválidos"""
        if not texto:
            return ""

        # Remover espaços e caracteres especiais problemáticos
        texto_limpo = texto.strip()

        # Manter apenas caracteres válidos
        resultado = ''.join(ch for ch in texto_limpo if regex_valido.match(ch))

        return resultado

    def continuar_busca(self):
        """Continua a busca removendo flag de pausa"""
        self.should_pause = False
        if 'pause_at' in self.cancel_flag:
            del self.cancel_flag['pause_at']

        print(f"[API SEARCH] 🔄 Continuando busca para {self.url}...")
        return self.buscar()

    def _buscar_banco_local(self) -> List[str]:
        """Busca logins no banco de dados local com busca mais abrangente"""
        try:
            db_file = "./database/bot_data.db"
            if not os.path.exists(db_file):
                print(f"[DB SEARCH] ⚠️ Banco de dados não encontrado: {db_file}")
                return []

            search_term = self.url.lower()
            results = []

            with sqlite3.connect(db_file) as conn:
                cursor = conn.cursor()

                # Verificar se é busca por extensão (*.gov, *.edu, etc.)
                if self.is_extension_search:
                    extension = self.url[2:]  # Remove "*."
                    query = """
                        SELECT login_data 
                        FROM logins 
                        WHERE LOWER(domain) LIKE ?
                        ORDER BY domain
                        LIMIT 50000
                    """
                    params = (f"%.{extension}",)
                    print(f"[DB SEARCH] 🔍 Buscando por extensão: {extension}")
                else:
                    # Busca MUITO mais abrangente para domínios
                    # Extrair partes do domínio para busca mais eficiente
                    domain_parts = search_term.split('.')
                    main_domain = domain_parts[0] if domain_parts else search_term

                    # Múltiplas estratégias de busca
                    queries_and_params = []

                    # 1. Busca exata
                    queries_and_params.append((
                        "SELECT login_data FROM logins WHERE LOWER(domain) = ?",
                        (search_term,)
                    ))

                    # 2. Busca por subdomínios (qualquer coisa.dominio.com)
                    queries_and_params.append((
                        "SELECT login_data FROM logins WHERE LOWER(domain) LIKE ?",
                        (f"%.{search_term}",)
                    ))

                    # 3. Busca pelo domínio principal (para pegar outras variações)
                    if len(domain_parts) > 1:
                        main_pattern = '.'.join(domain_parts[:-1])  # Remove extensão
                        queries_and_params.append((
                            "SELECT login_data FROM logins WHERE LOWER(domain) LIKE ?",
                            (f"{main_pattern}.%",)
                        ))

                    # 4. Busca pelo nome principal apenas (ex: sisregiii em qualquer domínio)
                    queries_and_params.append((
                        "SELECT login_data FROM logins WHERE LOWER(domain) LIKE ?",
                        (f"%{main_domain}%",)
                    ))

                    # 5. Para domínios gov.br, busca especial
                    if 'gov.br' in search_term or 'saude.gov.br' in search_term:
                        base_name = main_domain
                        queries_and_params.append((
                            "SELECT login_data FROM logins WHERE LOWER(domain) LIKE ? OR LOWER(domain) LIKE ?",
                            (f"%{base_name}%.gov.br", f"%{base_name}%.saude.gov.br")
                        ))

                    print(f"[DB SEARCH] 🔍 Busca abrangente para: {search_term}")
                    print(f"[DB SEARCH] 📊 Executando {len(queries_and_params)} estratégias de busca")

                    # Executar todas as queries e combinar resultados
                    all_results = set()  # Usar set para evitar duplicatas

                    for i, (query, params) in enumerate(queries_and_params):
                        try:
                            cursor.execute(query, params)
                            query_results = cursor.fetchall()
                            before_count = len(all_results)
                            all_results.update(row[0] for row in query_results)
                            new_count = len(all_results) - before_count
                            print(f"[DB SEARCH] 🎯 Estratégia {i+1}: +{new_count} novos logins (total: {len(query_results)})")
                        except Exception as e:
                            print(f"[DB SEARCH] ⚠️ Erro na estratégia {i+1}: {e}")

                    results = list(all_results)

                    # Se ainda poucos resultados, fazer busca mais ampla
                    if len(results) < 500 and main_domain:
                        print(f"[DB SEARCH] 🔄 Poucos resultados ({len(results)}), fazendo busca ultra-ampla...")
                        try:
                            cursor.execute(
                                "SELECT login_data FROM logins WHERE LOWER(domain) LIKE ? LIMIT 50000",
                                (f"%{main_domain}%",)
                            )
                            extra_results = cursor.fetchall()
                            before_extra = len(results)
                            all_results.update(row[0] for row in extra_results)
                            results = list(all_results)
                            extra_added = len(results) - before_extra
                            print(f"[DB SEARCH] 🚀 Busca ultra-ampla: +{extra_added} logins adicionais")
                        except Exception as e:
                            print(f"[DB SEARCH] ⚠️ Erro na busca ultra-ampla: {e}")

            print(f"[DB SEARCH] ✅ {len(results)} logins encontrados no banco local (busca abrangente)")
            return results

        except Exception as e:
            print(f"[DB SEARCH] ❌ Erro na busca local: {e}")
            logger.error(f"Erro na busca do banco local: {e}")
            return []

    def _adicionar_resultados_arquivos(self, results: List[str], raw_path: str, formatado_path: str, fonte: str) -> int:
        """Adiciona resultados aos arquivos e retorna quantidade adicionada"""
        if not results:
            return 0

        try:
            with open(raw_path, "a", encoding="utf-8") as f_raw, \
                 open(formatado_path, "a", encoding="utf-8") as f_fmt:

                contador_adicionado = 0
                for login_data in results:
                    if ':' in login_data:
                        # Escrever no arquivo raw
                        f_raw.write(f"{login_data}\n")

                        # Escrever no arquivo formatado
                        parts = login_data.split(':', 1)
                        if len(parts) >= 2:
                            email, senha = parts[0].strip(), parts[1].strip()
                            f_fmt.write(f"🔹 URL: {self.url}\n")
                            f_fmt.write(f"📧 EMAIL: {email}\n")
                            f_fmt.write(f"🔐 SENHA: {senha}\n")
                            f_fmt.write(f"📍 FONTE: {fonte}\n")
                            f_fmt.write(f"{'-'*50}\n\n")

                        contador_adicionado += 1

                print(f"[COMBINED SEARCH] ✅ {contador_adicionado} logins de {fonte} adicionados aos arquivos")
                return contador_adicionado

        except Exception as e:
            print(f"[COMBINED SEARCH] ❌ Erro ao adicionar resultados de {fonte}: {e}")
            return 0

    def _buscar_api_externa(self, raw_path: str, formatado_path: str, contador_inicial: int) -> int:
        """Busca logins na API externa e adiciona aos arquivos existentes"""
        contador = contador_inicial
        regex_valido = re.compile(r'^[a-zA-Z0-9!@#$%^&*()\-_=+\[\]{}|;:\'\",.<>/?`~\\]+$')
        max_retries = 3
        retry_count = 0

        while retry_count < max_retries and not self.cancel_flag.get('cancelled', False):
            try:
                print(f"[API SEARCH] 🔄 Tentativa {retry_count + 1}/{max_retries}")

                # Preparar URL da API
                if self.is_extension_search:
                    search_url = self.url[2:]  # Remove "*."
                    api_url = f"https://patronhost.online/logs/api_sse.php?url={search_url}"
                    print(f"[API SEARCH] 🔍 Busca por extensão: {search_url}")
                else:
                    api_url = f"https://patronhost.online/logs/api_sse.php?url={self.url}"
                    print(f"[API SEARCH] 🔍 Busca por domínio: {self.url}")

                # Headers otimizados
                headers = {
                    'Accept': 'text/event-stream',
                    'Cache-Control': 'no-cache',
                    'Connection': 'keep-alive',
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
                }

                # Fazer requisição com timeouts adequados
                print(f"[API SEARCH] 📡 Conectando à API...")
                response = requests.get(
                    api_url,
                    stream=True,
                    timeout=(10.0, 120.0),  # 10s conexão, 2min resposta
                    headers=headers,
                    verify=False  # Ignorar SSL se necessário
                )

                if response.status_code != 200:
                    print(f"[API SEARCH] ❌ Status HTTP: {response.status_code}")
                    response.raise_for_status()

                print(f"[API SEARCH] ✅ Conectado! Status: {response.status_code}")

                # Processar stream (modo append para adicionar aos resultados existentes)
                contador = self._processar_stream_append(response, raw_path, formatado_path, regex_valido, contador)

                print(f"[API SEARCH] ✅ Busca na API concluída! Total combinado: {contador} logins")
                break

            except requests.exceptions.Timeout:
                print(f"[API SEARCH] ⏰ Timeout na tentativa {retry_count + 1}")
                retry_count += 1
                if retry_count < max_retries:
                    print(f"[API SEARCH] 🔄 Aguardando 5s antes da próxima tentativa...")
                    time.sleep(5)

            except requests.exceptions.ConnectionError:
                print(f"[API SEARCH] 🔌 Erro de conexão na tentativa {retry_count + 1}")
                retry_count += 1
                if retry_count < max_retries:
                    print(f"[API SEARCH] 🔄 Aguardando 3s antes da próxima tentativa...")
                    time.sleep(3)

            except requests.exceptions.RequestException as e:
                print(f"[API SEARCH] ❌ Erro na requisição: {e}")
                retry_count += 1
                if retry_count < max_retries:
                    print(f"[API SEARCH] 🔄 Aguardando 2s antes da próxima tentativa...")
                    time.sleep(2)

            except Exception as e:
                print(f"[API SEARCH] ❌ Erro inesperado: {e}")
                logger.error(f"Erro inesperado na busca API: {e}")
                break

            finally:
                try:
                    if 'response' in locals():
                        response.close()
                except:
                    pass

        return contador

    def _processar_stream_append(self, response, raw_path, formatado_path, regex_valido, contador_inicial):
        """Processa o stream SSE e adiciona aos arquivos existentes (modo append)"""
        buffer = ""
        inactivity_timeout = 60  # 60 segundos sem dados
        chunk_size = 4096  # Chunks menores para melhor controle
        contador = contador_inicial

        print(f"[API SEARCH] 📊 Processando stream (modo append)...")

        try:
            with open(raw_path, "a", encoding="utf-8") as f_raw, \
                 open(formatado_path, "a", encoding="utf-8") as f_fmt:

                for chunk in response.iter_content(chunk_size=chunk_size, decode_unicode=True):
                    # Verificar cancelamento
                    if self.cancel_flag.get('cancelled', False):
                        print(f"[API SEARCH] 🛑 Busca cancelada pelo usuário")
                        break

                    if chunk:
                        buffer += chunk
                        self.last_activity = time.time()

                        # Processar linhas completas
                        while '\n' in buffer:
                            line, buffer = buffer.split('\n', 1)
                            line = line.strip()

                            if line and line.startswith('data: '):
                                event_data = line[6:].strip()

                                if self._processar_evento_append(event_data, f_raw, f_fmt, regex_valido):
                                    contador += 1

                                    # Callback para interface
                                    if self.contador_callback and (contador % 10 == 0 or contador < 100):
                                        try:
                                            self.contador_callback(contador)
                                        except Exception as e:
                                            print(f"[API SEARCH] ⚠️ Erro no callback: {e}")

                                    # Log de progresso
                                    if contador % 1000 == 0:
                                        elapsed = time.time() - self.start_time
                                        speed = contador / elapsed if elapsed > 0 else 0
                                        print(f"[COMBINED SEARCH] 📊 {contador:,} logins combinados em {elapsed:.1f}s ({speed:.1f}/s)")

                                    # Verificar pausa automática nos 20k logins
                                    if not self.disable_pause and contador > 0 and contador % self.pause_intervals == 0:
                                        print(f"[COMBINED SEARCH] ⏸️ Pausa automática aos {contador:,} logins combinados")
                                        self.cancel_flag['pause_at'] = contador
                                        # Finalizar arquivos
                                        f_raw.flush()
                                        f_fmt.flush()
                                        return contador

                                    # Verificar limite máximo
                                    if self.limite_max and contador >= self.limite_max:
                                        print(f"[COMBINED SEARCH] 🎯 Limite de {self.limite_max:,} logins atingido")
                                        return contador

                    else:
                        # Verificar timeout de inatividade
                        if time.time() - self.last_activity > inactivity_timeout:
                            print(f"[API SEARCH] ⏰ Timeout de inatividade ({inactivity_timeout}s)")
                            break

                # Processar buffer final
                if buffer.strip():
                    line = buffer.strip()
                    if line.startswith('data: '):
                        event_data = line[6:].strip()
                        if self._processar_evento_append(event_data, f_raw, f_fmt, regex_valido):
                            contador += 1

        except Exception as e:
            print(f"[API SEARCH] ❌ Erro ao processar stream: {e}")
            logger.error(f"Erro ao processar stream: {e}")

        return contador

    def _processar_evento_append(self, event_data: str, f_raw, f_fmt, regex_valido) -> bool:
        """Processa um evento SSE e adiciona aos arquivos (modo append)"""
        try:
            if not event_data or event_data.lower() in ['', 'null', 'undefined', 'none']:
                return False

            user_limpo = ""
            passwd_limpo = ""
            url_ = self.url

            # Tentar processar como JSON
            try:
                data = json.loads(event_data)
                if isinstance(data, dict):
                    url_ = data.get("url", self.url)
                    user = data.get("user", "")
                    passwd = data.get("pass", "")

                    if user and passwd and user.upper() not in ["EMPTY", "NULL", "UNDEFINED"]:
                        user_limpo = self._limpar_texto(user, regex_valido)
                        passwd_limpo = self._limpar_texto(passwd, regex_valido)

            except (json.JSONDecodeError, TypeError):
                # Processar como texto simples formato user:pass
                if ':' in event_data:
                    parts = event_data.split(':', 1)
                    if len(parts) == 2:
                        user_limpo = self._limpar_texto(parts[0], regex_valido)
                        passwd_limpo = self._limpar_texto(parts[1], regex_valido)

            # Validar dados
            if not user_limpo or not passwd_limpo:
                return False

            # Verificar se é email válido
            if '@' not in user_limpo:
                return False

            # Filtro inteligente por termo
            if self.search_term:
                search_content = f"{user_limpo.lower()} {passwd_limpo.lower()} {url_.lower()}"
                if self.search_term not in search_content:
                    return False

            # Escrever nos arquivos (modo append)
            f_raw.write(f"{user_limpo}:{passwd_limpo}\n")
            f_fmt.write(f"🔹 URL: {url_}\n")
            f_fmt.write(f"📧 EMAIL: {user_limpo}\n")
            f_fmt.write(f"🔐 SENHA: {passwd_limpo}\n")
            f_fmt.write(f"📍 FONTE: API EXTERNA\n")
            f_fmt.write(f"{'-'*50}\n\n")
            return True

        except Exception as e:
            print(f"[API SEARCH] ⚠️ Erro ao processar evento: {e}")
            return False

    def get_search_info(self):
        """Retorna informações sobre a busca atual"""
        return {
            'url': self.url,
            'user_id': self.id_user,
            'start_time': self.start_time,
            'end_time': self.end_time,
            'duration': (self.end_time - self.start_time) if self.end_time and self.start_time else None,
            'search_term': self.search_term,
            'is_extension_search': self.is_extension_search
        }
