"""
Coletor de artigos de feeds RSS.

Lê os feeds definidos em config.py, filtra por janela temporal,
baixa o texto completo de cada artigo e retorna uma lista normalizada.
"""

from datetime import datetime, timedelta, timezone
from time import mktime

import feedparser
import trafilatura

from config import FEEDS, HORAS_JANELA


# Limite de caracteres do resumo bruto do RSS (usado como fallback).
LIMITE_RESUMO = 1000

# Limite do texto completo extraído do artigo. 5000 chars cobre a maior
# parte de matérias de notícia sem estourar contexto do LLM.
LIMITE_TEXTO_COMPLETO = 5000


def _parse_data(entry):
    """
    Tenta extrair a data de publicação de uma entrada RSS.

    feedparser expõe `published_parsed` ou `updated_parsed` como struct_time.
    Retorna um datetime timezone-aware em UTC, ou None se não achar data.
    """
    for campo in ("published_parsed", "updated_parsed"):
        parsed = entry.get(campo)
        if parsed:
            return datetime.fromtimestamp(mktime(parsed), tz=timezone.utc)
    return None


def _limpar_resumo(texto):
    """Remove tags HTML grosseiras e trunca em LIMITE_RESUMO caracteres."""
    if not texto:
        return ""
    import re
    sem_tags = re.sub(r"<[^>]+>", "", texto).strip()
    if len(sem_tags) > LIMITE_RESUMO:
        sem_tags = sem_tags[:LIMITE_RESUMO].rstrip() + "..."
    return sem_tags


def _buscar_texto_completo(url):
    """
    Baixa o artigo da URL e extrai o texto principal via trafilatura.

    Retorna o texto truncado em LIMITE_TEXTO_COMPLETO, ou None se falhar.
    Trafilatura lida com cookie banners, menus, rodapés etc.
    """
    try:
        html = trafilatura.fetch_url(url)
        if not html:
            return None
        texto = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=False,
            no_fallback=False,
        )
        if not texto:
            return None
        texto = texto.strip()
        if len(texto) > LIMITE_TEXTO_COMPLETO:
            texto = texto[:LIMITE_TEXTO_COMPLETO].rstrip() + "..."
        return texto
    except Exception as e:
        print(f"  [fetch-err] {type(e).__name__}: {e}")
        return None


def coletar_artigos():
    """
    Coleta artigos recentes de todos os feeds configurados.

    Retorna lista de dicts com: fonte, titulo, link, resumo, texto_completo, data.
    `texto_completo` pode ser None se a extração falhar — nesse caso o
    `resumo` (teaser do RSS) deve ser usado como fallback pelo consumidor.
    """
    agora = datetime.now(timezone.utc)
    corte = agora - timedelta(hours=HORAS_JANELA)
    artigos = []

    print(f"[coleta] janela: últimas {HORAS_JANELA}h (desde {corte.isoformat()})")
    print(f"[coleta] processando {len(FEEDS)} feeds...")

    for nome, url, tier in FEEDS:
        try:
            feed = feedparser.parse(url)

            if feed.bozo and not feed.entries:
                motivo = getattr(feed, "bozo_exception", "erro desconhecido")
                print(f"[warn] {nome}: feed quebrado ({motivo})")
                continue

            novos = 0
            for entry in feed.entries:
                data = _parse_data(entry)
                if data is None:
                    continue
                if data < corte:
                    continue

                artigos.append({
                    "fonte": nome,
                    "tier": tier,
                    "titulo": entry.get("title", "(sem título)").strip(),
                    "link": entry.get("link", "").strip(),
                    "resumo": _limpar_resumo(entry.get("summary", "")),
                    "texto_completo": None,  # preenchido na etapa de fetch abaixo
                    "data": data,
                })
                novos += 1

            print(f"[ok]   {nome}: {novos} artigo(s) dentro da janela "
                  f"(total no feed: {len(feed.entries)})")

        except Exception as e:
            print(f"[warn] {nome}: falha ao processar — {type(e).__name__}: {e}")
            continue

    # Ordena com prioridade: primário antes de secundário; dentro de cada
    # grupo, mais recente antes. Isso preserva o viés de curadoria para
    # fonte primária quando o LLM for consumir a lista.
    _rank_tier = {"primario": 0, "secundario": 1}
    artigos.sort(key=lambda a: (_rank_tier.get(a["tier"], 9), -a["data"].timestamp()))

    # Etapa 2: baixa texto completo de cada artigo.
    if artigos:
        print(f"\n[fetch] baixando texto completo de {len(artigos)} artigo(s)...")
        sucessos = 0
        for i, art in enumerate(artigos, 1):
            if not art["link"]:
                print(f"  [{i}/{len(artigos)}] {art['fonte']}: sem link, pulando")
                continue
            texto = _buscar_texto_completo(art["link"])
            if texto:
                art["texto_completo"] = texto
                sucessos += 1
                print(f"  [{i}/{len(artigos)}] {art['fonte']}: "
                      f"ok ({len(texto)} chars)")
            else:
                print(f"  [{i}/{len(artigos)}] {art['fonte']}: "
                      f"falhou — usando resumo RSS")
        print(f"[fetch] texto completo: {sucessos}/{len(artigos)} artigo(s)")

    print(f"[coleta] total coletado: {len(artigos)} artigo(s)")
    return artigos


if __name__ == "__main__":
    resultados = coletar_artigos()
    print("\n" + "=" * 60)
    print(f"RESUMO: {len(resultados)} artigo(s) nas últimas {HORAS_JANELA}h")
    print("=" * 60)
    for i, art in enumerate(resultados, 1):
        status_texto = f"{len(art['texto_completo'])} chars" if art["texto_completo"] else "RSS fallback"
        print(f"\n[{i}] {art['fonte']} — {art['data'].strftime('%Y-%m-%d %H:%M UTC')} [{status_texto}]")
        print(f"    {art['titulo']}")
        print(f"    {art['link']}")
