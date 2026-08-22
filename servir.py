"""Serve site/ localmente, do jeito que o GitHub Pages serve.

O site é gerado para viver sob o `base_path` do config.json (em Pages de
repositório de projeto, `/<repo>/`), então todo link interno carrega esse
prefixo. Abrir site/index.html direto no navegador quebra tudo; servir a
pasta na raiz também. Este script tira o prefixo do caminho antes de
procurar o arquivo, e manda a raiz para o prefixo - exatamente o que o
Pages faz. Sem dependências, só para preview.

    python servir.py            # http://localhost:8000/<base_path>/
    python servir.py --porta 8080
"""

import argparse
import http.server
import json
import os

RAIZ = os.path.dirname(os.path.abspath(__file__))
DIR_SITE = os.path.join(RAIZ, "site")
ARQ_CONFIG = os.path.join(RAIZ, "config.json")


def base_path():
    """O mesmo prefixo que o gerador usa; vazio quando há domínio próprio."""
    if not os.path.exists(ARQ_CONFIG):
        return ""
    with open(ARQ_CONFIG, encoding="utf-8") as f:
        return json.load(f).get("base_path", "").rstrip("/")


class Handler(http.server.SimpleHTTPRequestHandler):
    prefixo = ""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIR_SITE, **kwargs)

    def do_GET(self):
        if self.prefixo and self.path in ("", "/"):
            self.send_response(302)
            self.send_header("Location", self.prefixo + "/")
            self.end_headers()
            return
        if self.prefixo and self.path.startswith(self.prefixo + "/"):
            self.path = self.path[len(self.prefixo) :]
        super().do_GET()

    def send_error(self, code, message=None, explain=None):
        # O Pages serve 404.html para qualquer caminho inexistente.
        pagina = os.path.join(DIR_SITE, "404.html")
        if code == 404 and os.path.exists(pagina):
            with open(pagina, "rb") as f:
                corpo = f.read()
            self.send_response(404)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(corpo)))
            self.end_headers()
            self.wfile.write(corpo)
            return
        super().send_error(code, message, explain)


def main():
    parser = argparse.ArgumentParser(description="Serve site/ como o GitHub Pages.")
    parser.add_argument("--porta", type=int, default=8000)
    args = parser.parse_args()
    if not os.path.isdir(DIR_SITE):
        print("site/ não existe; rode antes: python gerar_site.py")
        return 1
    Handler.prefixo = base_path()
    with http.server.ThreadingHTTPServer(("localhost", args.porta), Handler) as srv:
        print(f"http://localhost:{args.porta}{Handler.prefixo}/  (Ctrl+C para parar)")
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
