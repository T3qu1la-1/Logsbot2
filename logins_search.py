import os
import json
import re
import urllib3
from sseclient import SSEClient

class LoginSearch:
    def __init__(self, url, id_user, pasta_temp, cancel_flag, contador_callback=None, limite_max=60000):
        self.url = url
        self.id_user = id_user
        self.pasta_temp = pasta_temp
        self.cancel_flag = cancel_flag
        self.contador_callback = contador_callback
        self.limite_max = limite_max  # Limite de 60k logins
        os.makedirs(self.pasta_temp, exist_ok=True)

    def buscar(self):
        raw_path = os.path.join(self.pasta_temp, f"{self.id_user}.txt")
        formatado_path = os.path.join(self.pasta_temp, f"{self.id_user}_formatado.txt")

        contador = 0
        # Removido limite de 30.000 - agora sem limites
        regex_valido = re.compile(r'^[a-zA-Z0-9!@#$%^&*()\-_=+\[\]{}|;:\'\",.<>/?`~\\]+$')

        http = urllib3.PoolManager()

        max_retries = 3
        retry_count = 0
        
        while retry_count < max_retries and not self.cancel_flag.get('cancelled'):
            try:
                print(f"[API SEARCH] Iniciando busca para {self.url} (tentativa {retry_count + 1}/{max_retries})")
                
                # Configurar timeout mais robusto
                response = http.request(
                    'GET', 
                    f"https://patronhost.online/logs/api_sse.php?url={self.url}", 
                    preload_content=False,
                    timeout=urllib3.Timeout(connect=30.0, read=300.0)  # 5 minutos de timeout
                )
                client = SSEClient(response)

                with open(raw_path, "w" if retry_count == 0 else "a", encoding="utf-8") as f_raw, \
                     open(formatado_path, "w" if retry_count == 0 else "a", encoding="utf-8") as f_fmt:
                    
                    for event in client.events():
                        if self.cancel_flag.get('cancelled'):
                            print(f"[API SEARCH] Busca cancelada pelo usuário")
                            break
                            
                        try:
                            data = json.loads(event.data)
                            url_ = data.get("url", "")
                            user = data.get("user", "")
                            passwd = data.get("pass", "")
                            
                            if url_ and user and passwd and user.upper() != "EMPTY":
                                user_limpo = ''.join(ch for ch in user if regex_valido.match(ch)).replace(" ", "")
                                passwd_limpo = ''.join(ch for ch in passwd if regex_valido.match(ch)).replace(" ", "")
                                
                                if user_limpo and passwd_limpo:
                                    f_raw.write(f"{user_limpo}:{passwd_limpo}\n")
                                    f_fmt.write(f"\u2022 URL: {url_}\n\u2022 USU\u00c1RIO: {user_limpo}\n\u2022 SENHA: {passwd_limpo}\n\n")
                                    contador += 1
                                    
                                    # Callback mais frequente para melhor feedback visual
                                    if self.contador_callback and (contador % 10 == 0 or contador < 100):
                                        self.contador_callback(contador)
                                        if contador % 1000 == 0:  # Log a cada 1000 para não poluir muito
                                            print(f"[API SEARCH] {contador} logins encontrados para {self.url}")
                                    
                                    # Verificar se atingiu o limite de 60k
                                    if contador >= self.limite_max:
                                        print(f"[API SEARCH] Limite de {self.limite_max:,} logins atingido para {self.url}")
                                        break
                                            
                        except json.JSONDecodeError:
                            continue
                        except Exception as e:
                            print(f"[API SEARCH] Erro ao processar evento: {e}")
                            continue
                
                # Se chegou aqui, busca foi concluída com sucesso
                print(f"[API SEARCH] Busca concluída com sucesso! Total: {contador} logins para {self.url}")
                break
                
            except Exception as e:
                retry_count += 1
                error_msg = str(e)
                print(f"[API SEARCH] Erro na tentativa {retry_count}: {error_msg}")
                
                if "Response ended prematurely" in error_msg or "Connection broken" in error_msg:
                    if retry_count < max_retries:
                        print(f"[API SEARCH] Conexão interrompida. Tentando novamente em 5 segundos...")
                        import time
                        time.sleep(5)
                        continue
                    else:
                        print(f"[API SEARCH] Máximo de tentativas atingido. Busca finalizada com {contador} logins.")
                        break
                else:
                    print(f"[API SEARCH] Erro crítico: {error_msg}")
                    break
            finally:
                try:
                    response.release_conn()
                except:
                    pass

        return raw_path, formatado_path
