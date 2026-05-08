"""
Entrega do digest: site HTML (Netlify) + email (Resend).

Responsabilidades:
  - Renderizar o markdown do digest em HTML pra o site (com dropdowns)
  - Renderizar o markdown em HTML pra o email (cards, sem JS)
  - Gerar o index.html do site com o arquivo de edições
  - Fazer deploy do site no Netlify via API
  - Enviar o email via Resend

Separação interna em 3 blocos comentados:
  [SITE]    — rendering HTML pra Netlify
  [EMAIL]   — rendering HTML + envio via Resend
  [NETLIFY] — empacotamento zip + deploy
"""

import io
import os
import re
import zipfile
from collections import OrderedDict
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

_TZ_BR = ZoneInfo("America/Sao_Paulo")
from pathlib import Path

import markdown as md_lib
import requests
import resend
from dotenv import load_dotenv

load_dotenv(override=True)

PASTA_JORNAL      = Path(__file__).parent / "jornal"
NETLIFY_TOKEN     = os.getenv("NETLIFY_TOKEN")
NETLIFY_SITE_ID   = os.getenv("NETLIFY_SITE_ID")
REMETENTE_PADRAO  = "Jornal <onboarding@resend.dev>"

# ── Paleta compartilhada (site e email usam as mesmas cores) ──────────────────
_BG      = "#0f1115"
_CARD    = "#1a1d24"
_BORDER  = "#2a2e38"
_TEXT    = "#f5f6f8"
_MUTED   = "#d0d4de"
_ACCENT  = "#9ec5ff"


# Remove crases ao redor de links markdown — o LLM frequentemente envolve as
# fontes em ` [Nome](url) ` (code span), o que faz a markdown engine renderizar
# o texto literal "[Nome](url)" no lugar do link clicável.
_LINK_EM_CODE = re.compile(r"`(\s*\[[^\]]+\]\([^)]+\)(?:\s*/\s*\[[^\]]+\]\([^)]+\))*\s*)`")


def _desencodar_fontes(markdown_texto):
    return _LINK_EM_CODE.sub(lambda m: m.group(1).strip(), markdown_texto)

# ╔══════════════════════════════════════════════════════════════════════════════
# ║  SITE — rendering HTML para o Netlify
# ╚══════════════════════════════════════════════════════════════════════════════

_CSS_SITE = f"""
:root {{
  --bg: {_BG}; --card: {_CARD}; --border: {_BORDER};
  --text: {_TEXT}; --muted: {_MUTED}; --accent: {_ACCENT};
  --accent-hover: #a8ccff;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0; padding: 2rem 1rem 4rem;
  background: var(--bg); color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  font-size: 17px; line-height: 1.6;
}}
.container {{ max-width: 720px; margin: 0 auto; }}
h1 {{ font-size: 2rem; margin: 0 0 0.25rem; letter-spacing: -0.02em; }}
.periodo {{ color: var(--text); font-size: 0.95rem; margin-bottom: 2.5rem; }}
h2 {{
  font-size: 1.4rem; margin: 2.5rem 0 1rem;
  padding-bottom: 0.5rem; border-bottom: 1px solid var(--border);
  letter-spacing: -0.01em;
}}
.tldr {{
  background: var(--card); border-left: 3px solid var(--accent);
  padding: 1rem 1.25rem; border-radius: 6px; margin-bottom: 1.5rem;
}}
.tldr p {{ margin: 0.5rem 0; }}
details {{
  background: var(--card); border: 1px solid var(--border);
  border-radius: 8px; padding: 0.75rem 1.1rem;
  margin-bottom: 0.75rem; transition: border-color 0.15s;
}}
details:hover {{ border-color: #3a3f4d; }}
details[open] {{ border-color: var(--accent); }}
summary {{
  cursor: pointer; font-weight: 500; color: var(--text);
  list-style: none; padding: 0.25rem 0;
  position: relative; padding-right: 1.5rem;
}}
summary::-webkit-details-marker {{ display: none; }}
summary::after {{
  content: "+"; position: absolute; right: 0; top: 50%;
  transform: translateY(-50%); color: var(--muted);
  font-size: 1.3rem; font-weight: 300;
}}
details[open] summary::after {{ content: "−"; }}
details > p, details > div {{ margin-top: 0.75rem; color: var(--text); }}
a {{
  color: var(--accent); text-decoration: none;
  border-bottom: 1px solid transparent; transition: border-color 0.15s;
}}
a:hover {{ color: var(--accent-hover); border-bottom-color: var(--accent-hover); }}
em {{ color: var(--muted); font-style: normal; }}
.sem-destaque {{ color: var(--muted); font-style: italic; padding: 0.5rem 0; }}
.footer {{
  margin-top: 4rem; padding-top: 1.5rem;
  border-top: 1px solid var(--border);
  color: var(--muted); font-size: 0.85rem; text-align: center;
}}
ul.indice {{ list-style: none; padding: 0; }}
ul.indice li {{
  background: var(--card); border: 1px solid var(--border);
  border-radius: 8px; padding: 1rem 1.25rem; margin-bottom: 0.5rem;
  display: flex; justify-content: space-between;
  align-items: center; flex-wrap: wrap; gap: 0.5rem;
}}
ul.indice li:hover {{ border-color: var(--accent); }}
ul.indice a {{ font-weight: 500; }}
.periodo-item {{ color: var(--muted); font-size: 0.85rem; }}
.btn-arquivo {{
  display: inline-block; margin: 1rem 0 2rem;
  padding: 0.6rem 1.1rem; background: var(--card);
  border: 1px solid var(--accent); border-radius: 8px;
  color: var(--accent); font-weight: 500; font-size: 0.95rem;
  transition: background 0.15s, color 0.15s;
}}
.btn-arquivo:hover {{ background: var(--accent); color: var(--bg); border-bottom-color: var(--accent); }}
@media (max-width: 600px) {{
  body {{ font-size: 16px; padding: 1.5rem 0.75rem 3rem; }}
  h1 {{ font-size: 1.6rem; }}
  h2 {{ font-size: 1.2rem; }}
}}
"""

_PARAGRAFO_RE = re.compile(r"<p>(.*?)</p>", flags=re.DOTALL)


def _extrair_paragrafos(html):
    return _PARAGRAFO_RE.findall(html)


def _eh_sem_destaque(conteudo):
    return "sem destaques hoje." in re.sub(r"<[^>]+>", "", conteudo).strip().lower()


def _eh_gancho(conteudo):
    """Parágrafo com <strong> = primeiro parágrafo de um item."""
    return "<strong>" in conteudo.lower()


def _eh_citacao_solta(conteudo):
    """Parágrafo que é só link(s) — citação separada do corpo pelo LLM."""
    texto = re.sub(r"<[^>]+>", "", conteudo).strip()
    if not texto:
        return False
    links = re.findall(r"<a\s[^>]*>([^<]*)</a>", conteudo)
    if not links:
        return False
    sobra = texto
    for t in links:
        sobra = sobra.replace(t, "", 1)
    return len(re.sub(r"[\s/().,;—-]+", "", sobra)) <= 3


def _paragrafos_em_details(html_secao):
    """Transforma pares gancho+corpo em <details>. Cuida de citações soltas."""
    paragrafos = _extrair_paragrafos(html_secao)
    if not paragrafos:
        return html_secao

    blocos = []
    i = 0
    while i < len(paragrafos):
        atual = paragrafos[i]

        if _eh_sem_destaque(atual):
            blocos.append(f'<p class="sem-destaque">{atual}</p>')
            i += 1
            continue

        if _eh_citacao_solta(atual) and blocos and blocos[-1].startswith("<details>"):
            if blocos[-1].endswith("</div></details>"):
                blocos[-1] = blocos[-1][:-len("</div></details>")] + f" {atual}</div></details>"
                i += 1
                continue

        proximo = paragrafos[i + 1] if i + 1 < len(paragrafos) else None
        if _eh_gancho(atual) and proximo and not _eh_sem_destaque(proximo):
            blocos.append(
                f"<details><summary>{atual}</summary><div>{proximo}</div></details>"
            )
            i += 2
            continue

        blocos.append(f"<details><summary>{atual}</summary><div>{atual}</div></details>")
        i += 1

    return "".join(blocos)


def renderizar_edicao(markdown_texto, titulo="Jornal de IA"):
    """
    Converte markdown do digest → HTML standalone com dropdowns.
    Extrai período e modelo do cabeçalho `*...*` do markdown.
    """
    periodo = modelo_label = ""

    m = re.match(r"\*([^*]+)\*\s*\n", markdown_texto)
    if m:
        periodo = m.group(1).strip()
        markdown_texto = markdown_texto[m.end():]

    m2 = re.match(r"\*Processado por: ([^*]+)\*\s*\n", markdown_texto)
    if m2:
        modelo_label = m2.group(1).strip()
        markdown_texto = markdown_texto[m2.end():]

    markdown_texto = _desencodar_fontes(markdown_texto)
    markdown_texto = markdown_texto.replace("## TL;DR", "## Visão Geral")
    html_corpo = md_lib.markdown(markdown_texto, extensions=["extra"])
    partes = re.split(r"(<h2>.*?</h2>)", html_corpo, flags=re.DOTALL)
    resultado = []
    for i, parte in enumerate(partes):
        if i == 0:
            resultado.append(parte)
        elif parte.startswith("<h2>"):
            resultado.append(parte)
        else:
            anterior = partes[i - 1] if i > 0 else ""
            if "visão geral" in anterior.lower():
                resultado.append(f'<div class="tldr">{parte}</div>')
            else:
                resultado.append(_paragrafos_em_details(parte))

    modelo_tag = (
        f' &nbsp;·&nbsp; <code style="font-size:0.8rem;background:{_CARD};'
        f'padding:2px 7px;border-radius:4px;color:#c9ccd3;">{modelo_label}</code>'
    ) if modelo_label else ""
    periodo_html = f'<div class="periodo">{periodo}{modelo_tag}</div>' if periodo else ""
    botao_arquivo = '<a href="index.html" class="btn-arquivo">📚 Ver todas as edições</a>'

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{titulo}</title>
<style>{_CSS_SITE}</style>
</head>
<body>
<div class="container">
<h1>🤖 {titulo}</h1>
{periodo_html}
{botao_arquivo}
{"".join(resultado)}
{botao_arquivo}
<div class="footer">Jornal de IA · <a href="index.html">← todas as edições</a></div>
</div>
</body>
</html>"""


def renderizar_indice(entradas):
    """Gera index.html com a lista de edições (mais recente primeiro)."""
    itens = [
        f'<li><a href="{e["nome"]}">{e["titulo"]}</a>'
        f'<span class="periodo-item">{e["periodo"]}</span></li>'
        for e in entradas
    ] if entradas else ["<li class='sem-destaque'>Ainda sem edições.</li>"]

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Jornal de IA — Arquivo</title>
<style>{_CSS_SITE}</style>
</head>
<body>
<div class="container">
<h1>🤖 Jornal de IA</h1>
<div class="periodo">Arquivo de edições</div>
<ul class="indice">
{"".join(itens)}
</ul>
</div>
</body>
</html>"""


# ╔══════════════════════════════════════════════════════════════════════════════
# ║  EMAIL — rendering HTML + envio via Resend
# ╚══════════════════════════════════════════════════════════════════════════════

_CSS_EMAIL = f"""
<style>
  body {{
    margin: 0; padding: 0; background: {_BG}; color: {_TEXT};
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    font-size: 16px; line-height: 1.6;
  }}
  .wrapper {{ max-width: 680px; margin: 0 auto; padding: 32px 20px 48px; }}
  .header {{ border-bottom: 1px solid {_BORDER}; padding-bottom: 20px; margin-bottom: 28px; }}
  .header h1 {{ font-size: 26px; margin: 0 0 6px; color: #fff; letter-spacing: -0.02em; }}
  .header .meta {{ color: {_TEXT}; font-size: 14px; margin: 0; }}
  .banner {{
    background: #2a3f70; border: 1px solid #4a6db8; border-radius: 8px;
    padding: 14px 18px; margin-bottom: 16px; font-size: 15px; color: #fff;
  }}
  .banner a {{ color: #ffffff; font-weight: 700; text-decoration: underline; }}
  .btn-arquivo-email {{
    display: inline-block; padding: 12px 20px; margin-bottom: 28px;
    background: {_CARD}; border: 1px solid {_ACCENT}; border-radius: 8px;
    color: {_ACCENT} !important; font-weight: 600; text-decoration: none !important;
    font-size: 15px;
  }}
  h2 {{
    font-size: 20px; margin: 32px 0 14px;
    padding-bottom: 6px; border-bottom: 1px solid {_BORDER};
    color: #fff; letter-spacing: -0.01em;
  }}
  p {{ margin: 0 0 14px; color: {_TEXT}; }}
  a {{ color: {_ACCENT}; text-decoration: none; }}
  strong {{ color: #fff; font-weight: 600; }}
  em {{ color: {_MUTED}; font-style: normal; }}
  blockquote {{
    border-left: 3px solid {_ACCENT}; background: {_CARD};
    padding: 12px 18px; margin: 0 0 14px; border-radius: 0 6px 6px 0; color: #eef0f4;
  }}
  blockquote p {{ margin: 0; }}
  .item {{
    background: {_CARD}; border: 1px solid {_BORDER};
    border-radius: 8px; padding: 16px 20px; margin-bottom: 14px;
  }}
  .item .gancho {{ margin: 0 0 10px; padding-bottom: 10px; border-bottom: 1px solid {_BORDER}; color: #fff; }}
  .item .corpo {{ margin: 0; color: #eef0f4; font-size: 15px; }}
  .sem-destaque {{ color: {_MUTED}; font-style: italic; padding: 8px 0; }}
  .tldr {{
    background: {_CARD}; border-left: 3px solid {_ACCENT};
    padding: 14px 18px; border-radius: 0 6px 6px 0; margin-bottom: 16px;
  }}
  .tldr p {{ margin: 6px 0; }}
  ul {{ padding-left: 20px; }}
  li {{ margin-bottom: 6px; color: #eef0f4; }}
  .modelo {{
    display: inline-block; background: {_CARD};
    padding: 2px 8px; border-radius: 4px; color: #eef0f4;
    font-family: "SF Mono", Menlo, Consolas, monospace; font-size: 12px;
  }}
  .footer {{
    margin-top: 40px; padding-top: 20px; border-top: 1px solid {_BORDER};
    color: {_MUTED}; font-size: 13px; text-align: center; line-height: 1.7;
  }}
  .footer a {{ color: {_MUTED}; border-bottom: 1px dotted {_MUTED}; }}
</style>
"""


def _email_wrap_items(html_corpo):
    """Agrupa pares gancho+corpo em <div class='item'> pra o email."""
    partes = re.split(r"(<h2>.*?</h2>)", html_corpo, flags=re.DOTALL)
    resultado = []
    secao_atual = ""

    for i, parte in enumerate(partes):
        if parte.startswith("<h2>"):
            secao_atual = parte.lower()
            resultado.append(parte)
            continue
        if i == 0 and not parte.strip():
            continue
        if "visão geral" in secao_atual:
            resultado.append(f'<div class="tldr">{parte}</div>')
            continue
        if not parte.strip() or parte.strip().startswith("<blockquote"):
            resultado.append(parte)
            continue

        paragrafos = re.findall(r"<p>.*?</p>", parte, flags=re.DOTALL)
        if not paragrafos:
            resultado.append(parte)
            continue

        blocos = []
        j = 0
        while j < len(paragrafos):
            atual = paragrafos[j]
            texto = re.sub(r"<[^>]+>", "", atual).strip().lower()

            if "sem destaques hoje." in texto:
                blocos.append(atual.replace("<p>", '<p class="sem-destaque">'))
                j += 1
                continue

            if _eh_citacao_solta(atual) and blocos and blocos[-1].startswith('<div class="item">'):
                if blocos[-1].endswith("</div>"):
                    citacao = atual.replace(
                        "<p>",
                        '<p style="margin:10px 0 0;padding:6px 10px;'
                        'background:#0f1623;border-radius:4px;font-size:13px;">',
                        1,
                    )
                    blocos[-1] = blocos[-1][:-len("</div>")] + citacao + "</div>"
                    j += 1
                    continue

            proximo = paragrafos[j + 1] if j + 1 < len(paragrafos) else None
            proximo_e_gancho = proximo and "<strong>" in proximo.lower()
            _S_GANCHO = 'style="font-size:16px;color:#ffffff;margin:0 0 14px;padding-bottom:12px;border-bottom:1px solid #2a2e38;line-height:1.5;"'
            _S_CORPO  = 'style="font-size:15px;color:#d0d4de;margin:0;line-height:1.65;"'
            if proximo and "<strong>" in atual.lower() and not proximo_e_gancho and not _eh_citacao_solta(proximo):
                gancho = atual.replace("<p>", f'<p class="gancho" {_S_GANCHO}>', 1)
                corpo = proximo.replace("<p>", f'<p class="corpo" {_S_CORPO}>', 1)
                blocos.append(f'<div class="item">{gancho}{corpo}</div>')
                j += 2
            else:
                envolvido = atual.replace("<p>", f'<p class="corpo" {_S_CORPO}>', 1)
                blocos.append(f'<div class="item">{envolvido}</div>')
                j += 1

        resultado.append("".join(blocos))

    return "".join(resultado)


def _renderizar_email_html(markdown_texto, url_interativa=None, modelo=None, url_arquivo=None):
    """Converte markdown do digest → HTML estilizado pra email."""
    # Extrai período do cabeçalho antes de remover as linhas *...*
    periodo_extraido = ""
    m = re.match(r"^\*([^\n]+)\*\s*\n", markdown_texto)
    if m and "Período coberto" in m.group(1):
        periodo_extraido = m.group(1).strip()

    # Corrige texto legado "no digest" → "no jornal"
    periodo_extraido = periodo_extraido.replace("no digest", "no jornal")
    # Corrige TL;DR → Visão Geral em mds antigos
    markdown_texto = markdown_texto.replace("## TL;DR", "## Visão Geral")

    # Remove todas as linhas de cabeçalho *...* do markdown
    markdown_texto = re.sub(r"^(\*[^\n]+\*\s*\n)+", "", markdown_texto)
    markdown_texto = _desencodar_fontes(markdown_texto)

    corpo_html = md_lib.markdown(markdown_texto, extensions=["extra", "sane_lists"])
    corpo_html = _email_wrap_items(corpo_html)
    # Estiliza blockquotes de especialistas com inline styles
    corpo_html = re.sub(
        r"<blockquote>\s*<p>(.*?)</p>\s*</blockquote>",
        r'<blockquote style="border-left:3px solid #7c6fff;background:#1a1a2e;'
        r'padding:10px 16px;margin:10px 0 6px;border-radius:0 6px 6px 0;">'
        r'<p style="margin:0;font-size:14px;color:#d0d4de;font-style:italic;">\1</p></blockquote>',
        corpo_html, flags=re.DOTALL,
    )

    # Envolve a seção "Fontes do dia" numa caixa com fundo
    corpo_html = re.sub(
        r'(<h2[^>]*>Fontes do dia</h2>)(.*?)(?=<h2|$)',
        lambda m: m.group(1) + f'<div style="background:#1a1d24;border:1px solid #2a2e38;border-radius:8px;padding:14px 18px;margin-top:10px;font-size:14px;color:{_MUTED};line-height:2;">' + m.group(2) + '</div>',
        corpo_html, flags=re.DOTALL,
    )

    # Quando título e corpo estão no mesmo <p>, separa o <strong> em bloco
    def _split_gancho_corpo(m):
        before = m.group(1)
        titulo = m.group(2)
        resto  = m.group(3).strip()
        titulo_html = (
            f'<strong style="display:block;font-size:17px;color:#9ec5ff;'
            f'font-weight:700;margin-bottom:12px;padding-bottom:12px;'
            f'border-bottom:1px solid #2a2e38;line-height:1.4;">{titulo}</strong>'
        )
        corpo_html_inner = (
            f'<span style="font-size:15px;color:#d0d4de;line-height:1.65;">{resto}</span>'
            if resto else ""
        )
        return f"{before}{titulo_html}{corpo_html_inner}</p>"

    corpo_html = re.sub(
        r'(<p[^>]*>)<strong>(.*?)</strong>\s*(.+?)</p>',
        _split_gancho_corpo,
        corpo_html, flags=re.DOTALL,
    )
    # Inline styles nos h2 — Gmail ignora <style>, precisa ser inline
    corpo_html = re.sub(
        r"<h2>(.*?)</h2>",
        r'<h2 style="font-size:18px;color:#ffffff;background:#1e2a3a;'
        r'border-left:3px solid #9ec5ff;margin:28px 0 14px;'
        r'padding:10px 16px;border-radius:0 6px 6px 0;">\1</h2>',
        corpo_html,
    )

    data_curta = date.today().strftime("%d/%m/%y")
    data_longa  = date.today().strftime("%d/%m/%Y")

    modelo_badge = f'<span class="modelo">{modelo}</span>' if modelo else ""
    meta_partes = []
    if periodo_extraido:
        meta_partes.append(periodo_extraido)
    if modelo_badge:
        meta_partes.append(modelo_badge)
    meta_linha = " &nbsp;·&nbsp; ".join(meta_partes) if meta_partes else data_longa

    banner = (
        f'<div class="banner">📱 '
        f'<a href="{url_interativa}" style="color:#ffffff;font-weight:700;text-decoration:underline;">Abrir versão interativa com dropdowns</a>'
        f' — recomendado no celular.</div>'
    ) if url_interativa else ""

    botao_arquivo = (
        f'<a href="{url_arquivo}" class="btn-arquivo-email">📚 Ver todas as edições</a>'
    ) if url_arquivo else ""

    footer_partes = [f'Enviado em {datetime.now(timezone.utc).astimezone(_TZ_BR).strftime("%d/%m/%Y %H:%M")} (BRT)']
    if url_interativa:
        footer_partes.append(f'<a href="{url_interativa}">Ver versão interativa</a>')
    if url_arquivo:
        footer_partes.append(f'<a href="{url_arquivo}">Arquivo de edições anteriores</a>')
    footer_html = "<br>".join(footer_partes)

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="color-scheme" content="dark">
<title>Jornal {data_curta}</title>
{_CSS_EMAIL}
</head>
<body>
<div class="wrapper">
<div class="header" style="background:#1a2235;border-radius:10px;padding:20px 24px;margin-bottom:24px;border:1px solid #2d4780;">
<h1 style="font-size:26px;margin:0 0 8px;color:#ffffff;letter-spacing:-0.02em;">📰 Jornal {data_curta}</h1>
<p class="meta" style="color:{_TEXT};font-size:14px;margin:0;">{meta_linha}</p>
</div>
{banner}
{botao_arquivo}
{corpo_html}
<div class="footer">{footer_html}</div>
</div>
</body>
</html>"""


def enviar_email(assunto, corpo_markdown, assunto_padrao=True,
                 url_interativa=None, modelo=None, url_arquivo=None):
    """
    Envia o digest por email via Resend.

    Args:
        assunto:         Assunto do email. Se None, usa padrão por data.
        corpo_markdown:  Conteúdo em markdown (convertido pra HTML aqui).
        assunto_padrao:  Gera assunto padrão quando `assunto` é None.
        url_interativa:  URL do site Netlify (aparece como banner + footer).
        modelo:          Nome do modelo LLM usado (aparece no header do email).
    """
    api_key     = os.getenv("RESEND_API_KEY")
    destinatario = os.getenv("EMAIL_DESTINO")
    if not api_key:
        raise RuntimeError("RESEND_API_KEY não definida no .env")
    if not destinatario:
        raise RuntimeError("EMAIL_DESTINO não definida no .env")

    if not assunto and assunto_padrao:
        assunto = f"Jornal {date.today().strftime('%d/%m/%y')}"

    corpo_html = _renderizar_email_html(
        corpo_markdown, url_interativa=url_interativa, modelo=modelo,
        url_arquivo=url_arquivo,
    )

    resend.api_key = api_key
    print(f"[email] enviando via Resend para {destinatario}...")
    resposta = resend.Emails.send({
        "from": REMETENTE_PADRAO,
        "to": [destinatario],
        "subject": assunto,
        "html": corpo_html,
        "text": corpo_markdown,
    })
    email_id = resposta.get("id") if isinstance(resposta, dict) else None
    print(f"[email] enviado com sucesso. id={email_id}")


# ╔══════════════════════════════════════════════════════════════════════════════
# ║  NETLIFY — empacotamento + deploy
# ╚══════════════════════════════════════════════════════════════════════════════

def _parsear_nome(nome):
    """Extrai (data_iso, hora) de '2026-04-20_1430.md'."""
    m = re.match(r"(\d{4}-\d{2}-\d{2})_(\d{2})(\d{2})\.md$", nome)
    if not m:
        return None, None
    return m.group(1), f"{m.group(2)}:{m.group(3)}"


def _extrair_periodo(conteudo):
    """Pega '*Período coberto: ...*' do início do markdown."""
    m = re.match(r"\*([^*]+)\*", conteudo)
    return m.group(1).strip() if m else ""


def _gerar_arquivos():
    """Lê jornal/*.md e devolve {nome_html: conteudo} + lista pra índice."""
    arquivos, entradas = {}, []
    if not PASTA_JORNAL.exists():
        return arquivos, entradas

    for md_path in sorted(PASTA_JORNAL.glob("*.md"), reverse=True):
        conteudo = md_path.read_text(encoding="utf-8")
        data_iso, _ = _parsear_nome(md_path.name)
        if data_iso:
            data_br = datetime.strptime(data_iso, "%Y-%m-%d").strftime("%d/%m/%Y")
            titulo  = f"Jornal de IA — {data_br}"
        else:
            titulo = f"Jornal de IA — {md_path.stem}"

        nome_html = md_path.stem + ".html"
        arquivos[nome_html] = renderizar_edicao(conteudo, titulo)
        entradas.append({
            "nome": nome_html,
            "titulo": titulo,
            "periodo": _extrair_periodo(conteudo),
        })

    arquivos["index.html"] = renderizar_indice(entradas)
    # Força Content-Type correto no Netlify. Sem isso, em alguns deploys
    # (especialmente com zip de poucos arquivos) o .html vai como text/plain.
    arquivos["_headers"] = (
        "/*\n"
        "  Content-Type: text/html; charset=utf-8\n"
        "/*.html\n"
        "  Content-Type: text/html; charset=utf-8\n"
    )
    return arquivos, entradas


def _criar_zip(arquivos):
    # ZipInfo explícito com external_attr de arquivo regular (rw-r--r--).
    # Sem isso, o Netlify serve .html como text/plain porque o entry fica
    # sem os bits de "arquivo regular" e a heurística de content-type quebra.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for nome, conteudo in arquivos.items():
            info = zipfile.ZipInfo(nome)
            info.external_attr = 0o100644 << 16  # arquivo regular, perms 644
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, conteudo)
    buf.seek(0)
    return buf.read()


def publicar():
    """
    Renderiza todo o jornal e faz deploy no Netlify.
    Retorna (url_edicao_mais_recente, url_arquivo_indice).
    """
    if not NETLIFY_TOKEN or not NETLIFY_SITE_ID:
        raise RuntimeError("NETLIFY_TOKEN ou NETLIFY_SITE_ID não definidos no .env")

    print("[publisher] renderizando arquivos...")
    arquivos, entradas = _gerar_arquivos()
    if not arquivos:
        print("[publisher] nada para publicar.")
        return None

    print(f"[publisher] {len(arquivos)} arquivo(s) ({len(entradas)} edição(ões))")
    zip_bytes = _criar_zip(arquivos)
    print(f"[publisher] zip: {len(zip_bytes)/1024:.1f} KB — fazendo deploy...")

    resp = requests.post(
        f"https://api.netlify.com/api/v1/sites/{NETLIFY_SITE_ID}/deploys",
        headers={
            "Authorization": f"Bearer {NETLIFY_TOKEN}",
            "Content-Type": "application/zip",
        },
        data=zip_bytes,
        timeout=60,
    )
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Deploy falhou: {resp.status_code}: {resp.text[:300]}")

    dados    = resp.json()
    url_site = dados.get("ssl_url") or dados.get("url")
    print(f"[publisher] deploy ok: {url_site}")

    if entradas:
        url_edicao = f"{url_site}/{entradas[0]['nome']}"
        print(f"[publisher] edição mais recente: {url_edicao}")
        return url_edicao, url_site
    return url_site, url_site
