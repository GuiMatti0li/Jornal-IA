"""
Orquestrador do AI News Digest.

Fluxo:
  1. Coleta artigos recentes dos feeds RSS.
  2. Se vazio, envia email curto avisando e encerra.
  3. Gera resumo via LLM (Claude ou Gemini, conforme .env).
  4. Salva cópia local do resumo em jornal/AAAA-MM-DD_HHMM.md
  5. Envia resumo por email.
"""

import sys
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

from collector import coletar_artigos
from config import HORAS_JANELA
from delivery import publicar, enviar_email
from summarizer import DigestIncompletoError, _contar_itens, gerar_resumo, get_ultimo_modelo


PASTA_JORNAL = Path(__file__).parent / "jornal"

EMAIL_VAZIO = """## Sem artigos hoje

Nenhum artigo dentro da janela de 24 horas nos feeds monitorados.

Pode ser dia calmo, pode ser domingo, ou pode ser que os feeds estejam \
lentos. Amanhã tem mais.
"""


def _gerar_bloco_fontes(artigos):
    """
    Gera um bloco markdown com a contagem de notícias por fonte.

    Mostra apenas fontes com ao menos 1 artigo, ordenadas por contagem
    decrescente e, em caso de empate, por nome.
    """
    contagem = Counter(art["fonte"] for art in artigos if art.get("fonte"))
    if not contagem:
        return ""

    linhas = ["## Fontes do dia"]
    for fonte, total in sorted(contagem.items(), key=lambda item: (-item[1], item[0].lower())):
        sufixo = "notícia" if total == 1 else "notícias"
        linhas.append(f"- {fonte}: {total} {sufixo}")
    return "\n".join(linhas)


def salvar_no_jornal(conteudo_md):
    """
    Salva o digest em disco para histórico.

    Nome do arquivo usa data + hora (HHMM) pra múltiplas execuções no mesmo
    dia não sobrescreverem. Cria a pasta `jornal/` se não existir.
    """
    PASTA_JORNAL.mkdir(exist_ok=True)
    nome = datetime.now().strftime("%Y-%m-%d_%H%M.md")
    caminho = PASTA_JORNAL / nome
    caminho.write_text(conteudo_md, encoding="utf-8")
    print(f"[jornal] salvo em {caminho.relative_to(Path.cwd())}")
    return caminho


def main():
    print("=" * 60)
    print(f"AI News Digest — {date.today().isoformat()}")
    print("=" * 60)

    # Etapa 1: coleta
    print("\n[1/5] Coletando artigos...")
    artigos = coletar_artigos()

    # Etapa 2: caso vazio — ainda assim salva e envia o aviso
    if not artigos:
        print("\n[!] Nenhum artigo coletado. Enviando aviso curto.")
        salvar_no_jornal(EMAIL_VAZIO)
        assunto = f"Jornal de IA do dia {date.today().strftime('%d/%m/%y')} (sem artigos)"
        enviar_email(assunto, EMAIL_VAZIO)
        print("\n[fim] Execução concluída (sem artigos).")
        return

    # Etapa 3: resumo via LLM
    print(f"\n[2/5] Gerando resumo de {len(artigos)} artigo(s)...")
    try:
        resumo = gerar_resumo(artigos)
    except DigestIncompletoError as e:
        # Integridade falhou. MELHOR NÃO PUBLICAR do que publicar capenga —
        # um digest truncado no meio da frase destrói credibilidade.
        # Não salva em jornal/, não publica no Netlify, não envia email.
        print(f"[erro] Digest incompleto/truncado — abortando sem publicar.")
        print(f"[erro] Detalhe: {e}")
        print("[erro] Rode de novo mais tarde; nada foi enviado.")
        sys.exit(1)
    except Exception as e:
        print(f"[erro] Falha ao gerar resumo: {type(e).__name__}: {e}")
        sys.exit(1)

    # Prepende cabeçalho com o período coberto, contagem de itens e modelo.
    agora_local = datetime.now()
    inicio_local = agora_local - timedelta(hours=HORAS_JANELA)
    fmt = "%d/%m/%Y %H:%M"
    modelo_usado = get_ultimo_modelo()
    n_itens = _contar_itens(resumo)
    cabecalho = (
        f"*Período coberto: {inicio_local.strftime(fmt)} — "
        f"{agora_local.strftime(fmt)} "
        f"· {len(artigos)} artigos coletados · {n_itens} itens no digest*\n"
        f"*Processado por: {modelo_usado}*\n\n"
    )
    resumo = cabecalho + resumo

    bloco_fontes = _gerar_bloco_fontes(artigos)
    if bloco_fontes:
        resumo = f"{resumo}\n\n{bloco_fontes}\n"

    # Etapa 4: salva cópia local ANTES de enviar (garante arquivo mesmo se
    # o email falhar)
    print("\n[3/5] Salvando cópia em jornal/...")
    salvar_no_jornal(resumo)

    # Etapa 5: publica versão web no Netlify (não-crítico: se falhar,
    # segue em frente e manda o email sem link interativo)
    print("\n[4/5] Publicando versão web no Netlify...")
    url_interativa = None
    try:
        url_interativa = publicar()
    except Exception as e:
        print(f"[aviso] Falha ao publicar no Netlify: {type(e).__name__}: {e}")
        print("[aviso] Seguindo sem versão web.")

    # Etapa 6: envio
    print("\n[5/5] Enviando email...")
    modelo_usado = get_ultimo_modelo()
    try:
        enviar_email(None, resumo, url_interativa=url_interativa, modelo=modelo_usado)
    except Exception as e:
        print(f"[erro] Falha ao enviar email: {type(e).__name__}: {e}")
        sys.exit(1)

    print("\n[fim] Execução concluída com sucesso.")


if __name__ == "__main__":
    main()
