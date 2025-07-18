import os
import json
import re
import requests
import time
from typing import Dict, Callable, Optional

class LoginSearch:
    def __init__(self, url: str, id_user: int, pasta_temp: str, cancel_flag: Dict, contador_callback: Optional[Callable] = None, limite_max: Optional[int] = 50000):
        self.url = url
        self.id_user = id_user
        self.pasta_temp = pasta_temp
        self.cancel_flag = cancel_flag
        self.contador_callback = contador_callback
        self.limite_max = limite_max  # Limite padrão de 50k resultados
        os.makedirs(self.pasta_temp, exist_ok=True)

    def buscar(self):
        raw_path = os.path.join(self.pasta_temp, f"{self.id_user}.txt")
        formatado_path = os.path.join(self.pasta_temp, f"{self.id_user}_formatado.txt")

        contador = 0
        regex_valido = re.compile(r'^[a-zA-Z0-9!@#$%^&*()\-_=+\[\]{}|;:\'\",.<>/?`~\\]+$')

        max_retries = 3  # Tentativas balanceadas
        retry_count = 0

        while retry_count < max_retries and not self.cancel_flag.get('cancelled'):
            try:
                print(f"[API SEARCH] Iniciando busca para {self.url} (tentativa {retry_count + 1}/{max_retries})")

                # Fazer requisição com stream - timeouts balanceados
                response = requests.get(
                    f"https://patronhost.online/logs/api_sse.php?url={self.url}",
                    stream=True,
                    timeout=(15.0, 300.0),  # 15s conexão + 5min resposta (tempo adequado)
                    headers={
                        'Accept': 'text/event-stream',
                        'Cache-Control': 'no-cache',
                        'Connection': 'keep-alive',
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                    }
                )
                
                response.raise_for_status()

                with open(raw_path, "w" if retry_count == 0 else "a", encoding="utf-8") as f_raw, \
                     open(formatado_path, "w" if retry_count == 0 else "a", encoding="utf-8") as f_fmt:

                    # Processar linha por linha do stream
                    buffer = ""
                    last_data_time = time.time()
                    inactivity_timeout = 90  # 90 segundos sem dados = parar busca
                    
                    for chunk in response.iter_content(chunk_size=8192, decode_unicode=True):  # Chunks maiores
                        if self.cancel_flag.get('cancelled'):
                            print(f"[API SEARCH] Busca cancelada pelo usuário")
                            break

                        if chunk:
                            buffer += chunk
                            last_data_time = time.time()  # Resetar timer de inatividade
                            
                            # Processar dados quando tiver chunk
                            lines = buffer.split('\n')
                            buffer = lines[-1]  # Manter a última linha incompleta no buffer
                            
                            for line in lines[:-1]:
                                line = line.strip()
                                if not line:
                                    continue
                                
                                # Processar eventos SSE
                                if line.startswith('data: '):
                                    event_data = line[6:].strip()
                                    if self._processar_evento(event_data, f_raw, f_fmt, regex_valido):
                                        contador += 1
                                        
                                        # Callback e verificações
                                        if self.contador_callback and (contador % 10 == 0 or contador < 100):
                                            self.contador_callback(contador)
                                        
                                        if self.limite_max and contador >= self.limite_max:
                                            print(f"[API SEARCH] Limite de {self.limite_max:,} logins atingido para {self.url}")
                                            return raw_path, formatado_path
                        else:
                            # Verificar timeout de inatividade apenas quando não há dados
                            if time.time() - last_data_time > inactivity_timeout:
                                print(f"[API SEARCH] Timeout de inatividade atingido para {self.url}")
                                break

                    # Processar buffer restante
                    if buffer.strip():
                        if buffer.startswith('data: '):
                            event_data = buffer[6:].strip()
                            if self._processar_evento(event_data, f_raw, f_fmt, regex_valido):
                                contador += 1

                print(f"[API SEARCH] Busca concluída com sucesso! Total: {contador} logins para {self.url}")
                break

            except Exception as e:
                retry_count += 1
                error_msg = str(e)
                print(f"[API SEARCH] Erro na tentativa {retry_count}: {error_msg}")

                if retry_count < max_retries:
                    print(f"[API SEARCH] Tentando novamente em 3 segundos...")
                    time.sleep(3)  # Delay adequado para reconexão
                else:
                    print(f"[API SEARCH] Máximo de tentativas atingido. Busca finalizada com {contador} logins.")
                    break
            finally:
                try:
                    response.close()
                except:
                    pass

        return raw_path, formatado_path

    def _processar_evento(self, event_data: str, f_raw, f_fmt, regex_valido) -> bool:
        """Processa um evento SSE e retorna True se um login válido foi encontrado"""
        try:
            if not event_data or event_data in ['', 'null', 'undefined']:
                return False
            
            # Tentar como JSON primeiro
            try:
                data = json.loads(event_data)
                if isinstance(data, dict):
                    url_ = data.get("url", "")
                    user = data.get("user", "")
                    passwd = data.get("pass", "")

                    if url_ and user and passwd and user.upper() != "EMPTY":
                        user_limpo = ''.join(ch for ch in user if regex_valido.match(ch)).replace(" ", "")
                        passwd_limpo = ''.join(ch for ch in passwd if regex_valido.match(ch)).replace(" ", "")

                        if user_limpo and passwd_limpo:
                            f_raw.write(f"{user_limpo}:{passwd_limpo}\n")
                            f_fmt.write(f"• URL: {url_}\n• USUÁRIO: {user_limpo}\n• SENHA: {passwd_limpo}\n\n")
                            return True
            except json.JSONDecodeError:
                # Se não for JSON, tentar como formato user:pass
                if ':' in event_data:
                    parts = event_data.split(':', 1)
                    if len(parts) == 2:
                        user_limpo = ''.join(ch for ch in parts[0] if regex_valido.match(ch)).replace(" ", "")
                        passwd_limpo = ''.join(ch for ch in parts[1] if regex_valido.match(ch)).replace(" ", "")
                        
                        if user_limpo and passwd_limpo:
                            f_raw.write(f"{user_limpo}:{passwd_limpo}\n")
                            f_fmt.write(f"• URL: {self.url}\n• USUÁRIO: {user_limpo}\n• SENHA: {passwd_limpo}\n\n")
                            return True
            
            return False
            
        except Exception as e:
            print(f"[API SEARCH] Erro ao processar evento: {e}")
            return False
