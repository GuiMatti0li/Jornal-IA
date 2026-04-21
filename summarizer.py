"""
Geração de resumo diário via LLM.

Suporta dois provedores, selecionados pela variável LLM_PROVIDER no .env:
  - "anthropic": Claude Sonnet 4.6 (padrão, pago ~$0.10/run)
  - "gemini":    Gemini 2.5 Flash (grátis no tier free do Google AI Studio)

Ambos recebem o mesmo prompt e devolvem markdown no mesmo formato.
"""

import os
import re
import time

import anthropic
from dotenv import load_dotenv
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types

load_dotenv(override=True)

# Provedor escolhido (case-insensitive). Padrão: anthropic.
PROVEDOR = os.getenv("LLM_PROVIDER", "anthropic").strip().lower()

# Modelos fixados por provedor.
MODELO_ANTHROPIC = "claude-sonnet-4-6"
MODELO_GEMINI = "gemini-2.5-flash"

MAX_TOKENS = 16000
MAX_TENTATIVAS_RESUMO = 2
MAX_CONTINUACOES = 2  # quantas vezes podemos pedir "continue" antes de desistir

# Guarda o modelo efetivamente usado na última geração. Isso é importante
# porque o fallback Gemini→Anthropic muda o modelo em runtime, e o resto
# do pipeline (email, jornal) quer reportar o modelo real, não o configurado.
_ULTIMO_MODELO = None


def get_ultimo_modelo():
    """Retorna nome amigável do modelo usado na última geração."""
    return _ULTIMO_MODELO or "desconhecido"


class DigestIncompletoError(RuntimeError):
    """Levantada quando o digest gerado não passa na validação de integridade."""

SYSTEM_PROMPT = """Você é um analista técnico que prepara um digest diário de notícias de IA \
para um estudante de engenharia de computação. Escreve em português brasileiro, \
com tom direto e substantivo — sem hype, sem marketing, sem adjetivos vazios. \
Prioriza o que importa para alguém que quer entender o estado da arte e as \
forças moldando a área, não quem gosta de fofoca de Vale do Silício."""

INSTRUCOES = """Abaixo está uma lista de artigos coletados nas últimas 24 horas de feeds \
sobre IA. Seu trabalho NÃO é agregar tudo — é CURAR. Um bom digest recusa \
incluir itens fracos.

PASSO 1 — CLASSIFICAÇÃO INTERNA (não escreva isso na resposta):
Para cada artigo, classifique mentalmente em:
  - Tier A (entra sempre): paper com resultado novo, lançamento de modelo \
ou produto com consequência técnica real, regulação concreta, movimento \
de mercado com impacto material.
  - Tier B (entra se houver espaço): análise bem fundamentada, tutorial \
substantivo, movimento menor mas verificável, feature notável.
  - Tier C (só em dia vazio): especulação, reconstrução não oficial, \
opinião, drama corporativo, rumor, anúncio de parceria genérica.

Regra de inclusão: inclua TODOS os Tier A e TODOS os Tier B, sem exceção \
de quantidade. Se Tier A + Tier B juntos resultarem em menos de 20 itens, \
complete com os melhores Tier C disponíveis até atingir 20 itens (ou até \
esgotar os artigos). Tier C irrelevante demais pode ser omitido mesmo assim \
— não precisa justificar, apenas omita.

PASSO 2 — ESTRUTURA:

## TL;DR
EXATAMENTE 3 frases:
  1. Tese do dia em uma frase (se teve tema dominante, qual).
  2. Segundo eixo, contraponto, ou "foi um dia pulverizado".
  3. Começar com "Implicação prática:" ou "O que observar:" — uma frase \
orientada a DECISÃO, não a fato. O que alguém construindo produto ou \
planejando carreira deveria tirar disso.

## 🔬 Técnico
Papers, novos modelos, benchmarks, avanços de arquitetura, resultados \
empíricos.

## 🚀 Produto
Lançamentos, features, integrações, movimentos de empresas com \
consequência real para produto. Corte drama corporativo puro.

## 🌍 Sociedade
Regulação, ética, impacto no trabalho, segurança, política pública.

PASSO 3 — FORMATO DE CADA ITEM:

Cada item tem DOIS parágrafos separados por UMA linha em branco.

Parágrafo 1 (GANCHO) — é o que aparece fechado no dropdown:
  - Começa com `**título curto**` (4 a 10 palavras, substantivo, \
provocador mas não clickbait). Se o item é especulação ou não confirmado, \
prefixe o título com `[Sinal fraco]` ou `[Não confirmado]`.
  - Após o título, 1 frase (máx 25 palavras) que desperta interesse \
mostrando RELEVÂNCIA ou CONSEQUÊNCIA — não detalhe técnico.
  - Linguagem acessível: evita jargão, siglas obscuras, hiperparâmetros \
e números jogados sem contexto. Alguém que não leu o paper tem que \
entender por que importa.
  - Exemplo BOM: "**Huawei propõe HiFloat4 para treinar LLMs em NPUs \
chineses** — formato numérico co-desenvolvido com o silício sinaliza \
como o embargo de GPUs está reorganizando a pilha técnica."

Parágrafo 2 (CORPO) — aparece ao expandir:
  - 4 a 7 frases com profundidade: contribuição, por que importa, pelo \
menos um detalhe técnico concreto (número, mecanismo, comparação com \
baseline, arquitetura).
  - A citação vai no FINAL do parágrafo do CORPO, NA MESMA LINHA do \
texto, sem quebra de parágrafo antes. NUNCA coloque a citação em uma \
linha separada ou parágrafo próprio — ela é parte da última frase do \
corpo. Use SEMPRE o nome real da fonte (campo FONTE do artigo) como \
texto do link — NUNCA a palavra genérica "Fonte". Formato: \
`[Nome da Fonte](url)`.
  - Exemplos certos: `[Anthropic](https://...)`, \
`[MIT Tech Review](https://...)`, `[Import AI](https://...)`.
  - Exemplo ERRADO (não faça): `[Fonte](https://...)`.
  - Se o item agrega 2+ artigos, liste todas as fontes separadas por \
` / ` (espaço-barra-espaço). Exemplo: \
`[Anthropic](https://...) / [Import AI](https://...)`.
  - NÃO repita a frase ou formulação do gancho. Continue a história, \
não recomece.

PASSO 4 — REGRAS EDITORIAIS:

- Um tema aparece em UMA seção só. Se um item poderia entrar em duas \
(ex: regulação que afeta produto), escolha a principal e mencione o \
outro ângulo em uma frase dentro do corpo. NÃO duplique.
- Um item = um tema central. Se dois artigos são do mesmo eixo \
(ex: dois papers sobre KV cache), junte em UM item com ambos os links. \
Se são temas diferentes, separe em itens distintos — não force junção.
- Se uma seção não tem Tier A nem Tier B, escreva apenas: \
`_Sem destaques hoje._`
- Não invente informação. Se um artigo é só headline sem substância, \
ele é Tier C e é omitido — não faça item fraco "pra não desperdiçar".
- O parágrafo 1 SEMPRE usa `**...**` no título (vira `<strong>` no HTML).
- Cada seção (`## TL;DR`, `## 🔬 Técnico`, `## 🚀 Produto`, \
`## 🌍 Sociedade`) aparece EXATAMENTE UMA VEZ. Nunca repita um \
cabeçalho de seção. Se tiver muitos itens em Técnico, coloque-os todos \
em sequência dentro da mesma seção, sem abrir um segundo `## 🔬 Técnico`.

ARTIGOS:

{lista_formatada}"""

PROMPT_CORRECAO_COBERTURA = """

REVISÃO OBRIGATÓRIA DA RESPOSTA ANTERIOR:
- A resposta anterior ficou subcoberta para o volume de artigos recebido.
- Refaça o digest do zero, sem resumir demais.
- Cubra os temas realmente importantes do dia em múltiplos itens quando necessário.
- Mantenha a estrutura exigida com TL;DR + seções + itens em dois parágrafos.
- Não encerre cedo; entregue um digest completo antes de finalizar.
"""


def _formatar_artigos(artigos):
    """
    Formata a lista de artigos em texto plano para o prompt.

    Prefere `texto_completo` (extraído via trafilatura) e cai no `resumo` do
    RSS se a extração tiver falhado.
    """
    blocos = []
    for art in artigos:
        data_fmt = art["data"].strftime("%Y-%m-%d %H:%M UTC")
        texto = art.get("texto_completo") or art.get("resumo") or "(sem conteúdo disponível)"
        origem = "artigo completo" if art.get("texto_completo") else "resumo RSS"
        bloco = (
            f"---\n"
            f"FONTE: {art['fonte']}\n"
            f"TÍTULO: {art['titulo']}\n"
            f"LINK: {art['link']}\n"
            f"DATA: {data_fmt}\n"
            f"CONTEÚDO ({origem}):\n{texto}"
        )
        blocos.append(bloco)
    return "\n".join(blocos)


def _gerar_via_anthropic(prompt_usuario):
    """
    Gera resumo usando Claude Sonnet via Anthropic API.

    Retorna tupla (texto, foi_truncado). `foi_truncado=True` quando o
    modelo bateu o teto de max_tokens — o chamador deve pedir continuação.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY não definida no .env")

    print(f"[resumo] provedor=anthropic, modelo={MODELO_ANTHROPIC}")
    global _ULTIMO_MODELO
    _ULTIMO_MODELO = f"Claude Sonnet 4.6 ({MODELO_ANTHROPIC})"
    cliente = anthropic.Anthropic(api_key=api_key)
    resposta = cliente.messages.create(
        model=MODELO_ANTHROPIC,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt_usuario}],
    )
    uso = resposta.usage
    truncado = resposta.stop_reason == "max_tokens"
    marca = " [TRUNCADO]" if truncado else ""
    print(
        f"[resumo] tokens: entrada={uso.input_tokens}, "
        f"saída={uso.output_tokens}{marca}"
    )
    return resposta.content[0].text, truncado


def _gerar_via_gemini(prompt_usuario):
    """
    Gera resumo usando Gemini 2.5 Flash via Google AI Studio (tier grátis).

    Faz retry em erros transitórios (503 UNAVAILABLE, 429 RESOURCE_EXHAUSTED)
    comuns em horário de pico. Até 3 tentativas com backoff 10s → 30s.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY não definida no .env")

    print(f"[resumo] provedor=gemini, modelo={MODELO_GEMINI}")
    global _ULTIMO_MODELO
    _ULTIMO_MODELO = f"Gemini 2.5 Flash ({MODELO_GEMINI})"
    cliente = genai.Client(api_key=api_key)

    tentativas = 3
    backoff = [0, 10, 30]
    erro_transitorio = (503, 429)

    for i in range(tentativas):
        if backoff[i] > 0:
            print(
                f"[resumo] aguardando {backoff[i]}s antes de retry "
                f"(tentativa {i + 1}/{tentativas})..."
            )
            time.sleep(backoff[i])
        try:
            resposta = cliente.models.generate_content(
                model=MODELO_GEMINI,
                contents=prompt_usuario,
                config=genai_types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    max_output_tokens=MAX_TOKENS,
                ),
            )
            uso = resposta.usage_metadata
            # Detecta truncamento: finish_reason "MAX_TOKENS" indica teto atingido.
            truncado = False
            try:
                finish = str(resposta.candidates[0].finish_reason)
                truncado = "MAX_TOKENS" in finish.upper()
            except (AttributeError, IndexError, TypeError):
                pass
            marca = " [TRUNCADO]" if truncado else ""
            if uso:
                print(
                    f"[resumo] tokens: entrada={uso.prompt_token_count}, "
                    f"saída={uso.candidates_token_count}{marca}"
                )
            return resposta.text, truncado
        except (genai_errors.ServerError, genai_errors.ClientError) as e:
            codigo = getattr(e, "code", None)
            if codigo in erro_transitorio and i < tentativas - 1:
                print(f"[resumo] erro transitório {codigo}, tentando de novo...")
                continue
            raise


def _validar_integridade(resumo_markdown):
    """
    Checa se o digest parece completo (não truncado no meio).

    Retorna lista de erros. Vazia = íntegro.

    Checagens:
      - Termina com pontuação de fim (. ! ? " ) _ * `)
      - Última linha não termina em — ou : (meio de frase/lista)
      - Todas as 4 seções obrigatórias existem e têm algum conteúdo
        (ou o marcador explícito "Sem destaques hoje.")
    """
    erros = []
    texto = resumo_markdown.rstrip()
    if not texto:
        return ["resumo vazio"]

    ultimo_char = texto[-1]
    if ultimo_char not in '.!?")_`*':
        erros.append(
            f"não termina com pontuação final (último char: {ultimo_char!r})"
        )

    ultima_linha = texto.splitlines()[-1].rstrip()
    if ultima_linha.endswith(("—", "-", ":", ",", ";")):
        erros.append(
            f"última linha termina em caractere de continuação: {ultima_linha[-1]!r}"
        )

    # Cada seção obrigatória deve ter ao menos 1 linha de conteúdo depois.
    obrigatorios = ("## TL;DR", "## 🔬 Técnico", "## 🚀 Produto", "## 🌍 Sociedade")
    for secao in obrigatorios:
        # Acha a seção e verifica se tem conteúdo até o próximo ## ou fim.
        idx = resumo_markdown.find(secao)
        if idx == -1:
            erros.append(f"seção ausente: {secao}")
            continue
        depois = resumo_markdown[idx + len(secao):]
        prox_secao = depois.find("\n## ")
        conteudo = depois[:prox_secao] if prox_secao != -1 else depois
        conteudo_limpo = conteudo.strip()
        if not conteudo_limpo:
            erros.append(f"seção sem conteúdo: {secao}")

    return erros


def _mesclar_secoes_duplicadas(resumo_markdown):
    """
    Se o LLM repetir um cabeçalho de seção (ex: dois '## 🔬 Técnico'),
    mescla o conteúdo das ocorrências duplicadas na primeira.

    Isso acontece quando o output é muito longo ou quando a continuação
    automática reabre uma seção já fechada.
    """
    # Divide o markdown em (cabeçalho, conteúdo) preservando a ordem
    partes = re.split(r"(^##\s.+$)", resumo_markdown, flags=re.MULTILINE)
    # partes = ['preâmbulo', '## TL;DR', '\nconteudo', '## Técnico', '\nconteudo', ...]

    # Agrega conteúdo por cabeçalho, mantendo a primeira ocorrência de cada
    from collections import OrderedDict
    ordem = []
    conteudos = OrderedDict()

    # Primeiro elemento é o preâmbulo (antes do primeiro ##)
    preambulo = partes[0] if partes else ""

    i = 1
    while i < len(partes) - 1:
        cabecalho = partes[i].strip()
        conteudo = partes[i + 1]
        if cabecalho not in conteudos:
            ordem.append(cabecalho)
            conteudos[cabecalho] = conteudo
        else:
            # Seção duplicada — concatena o conteúdo extra na primeira
            conteudos[cabecalho] += conteudo
            duplicatas = cabecalho
            print(f"[resumo] seção duplicada mesclada: {duplicatas}")
        i += 2

    if not conteudos:
        return resumo_markdown  # nada a mesclar

    resultado = preambulo
    for cab in ordem:
        resultado += cab + "\n" + conteudos[cab]
    return resultado


def _contar_itens(resumo_markdown):
    """Conta itens pelo número de títulos em negrito no começo do gancho."""
    return len(re.findall(r"(?m)^\*\*.+?\*\*", resumo_markdown))


def _contar_citacoes(resumo_markdown):
    """Conta links markdown usados como citação de fonte."""
    return len(re.findall(r"\[[^\]]+\]\(https?://[^)]+\)", resumo_markdown))


def _validar_resumo(resumo_markdown, quantidade_artigos):
    """
    Faz uma checagem heurística para detectar resumos claramente incompletos.

    A ideia não é impor formato rígido demais, só barrar casos em que o LLM
    devolve um digest muito curto para um lote grande de artigos.
    """
    erros = []
    obrigatorios = ("## TL;DR", "## 🔬 Técnico", "## 🚀 Produto", "## 🌍 Sociedade")
    faltando = [secao for secao in obrigatorios if secao not in resumo_markdown]
    if faltando:
        erros.append("seções obrigatórias ausentes")

    itens = _contar_itens(resumo_markdown)
    citacoes = _contar_citacoes(resumo_markdown)

    minimo_itens = 1 if quantidade_artigos <= 4 else min(15, max(3, int(quantidade_artigos * 0.4)))
    minimo_citacoes = 1 if quantidade_artigos <= 3 else min(10, max(2, quantidade_artigos // 5))

    if itens < minimo_itens:
        erros.append(f"itens insuficientes ({itens} < {minimo_itens})")
    if citacoes < minimo_citacoes:
        erros.append(f"citações insuficientes ({citacoes} < {minimo_citacoes})")

    return erros


def _chamar_provedor(prompt_usuario):
    """
    Chamada bruta ao provedor configurado (uma única vez, sem continuação).
    Retorna tupla (texto, foi_truncado).
    """
    if PROVEDOR == "anthropic":
        return _gerar_via_anthropic(prompt_usuario)
    if PROVEDOR == "gemini":
        try:
            return _gerar_via_gemini(prompt_usuario)
        except Exception as e:
            if not os.getenv("ANTHROPIC_API_KEY"):
                print("[resumo] Gemini falhou e não há ANTHROPIC_API_KEY pra fallback — abortando.")
                raise
            print(
                f"[resumo] Gemini falhou ({type(e).__name__}). "
                "Fallback automático: tentando Anthropic..."
            )
            return _gerar_via_anthropic(prompt_usuario)

    raise ValueError(
        f"LLM_PROVIDER inválido no .env: '{PROVEDOR}'. "
        "Use 'anthropic' ou 'gemini'."
    )


def _montar_prompt_continuacao(prompt_original, texto_parcial):
    """
    Monta um prompt que pede continuação a partir exatamente de onde
    o modelo parou, sem repetir nem reiniciar o digest.
    """
    return (
        f"{prompt_original}\n\n"
        "---\n"
        "A sua resposta anterior foi truncada por limite de tokens. "
        "Continue EXATAMENTE de onde parou — não repita nada, não "
        "recomece com '## TL;DR', não reescreva seções já feitas. "
        "Apenas continue do caractere exato em que a frase abaixo foi "
        "cortada, mantendo formato e estilo. Se estava no meio de uma "
        "palavra, complete a palavra primeiro.\n\n"
        "TEXTO ATÉ AGORA (últimos 2000 caracteres):\n"
        f"{texto_parcial[-2000:]}\n\n"
        "CONTINUE DAQUI:"
    )


def _gerar_texto_base(prompt_usuario):
    """
    Gera texto completo, reconstruindo em caso de truncamento.

    Se o provedor truncar, faz até MAX_CONTINUACOES chamadas adicionais
    pedindo continuação, concatenando os pedaços. Retorna só o texto
    final (já juntado).
    """
    texto, truncado = _chamar_provedor(prompt_usuario)

    continuacoes = 0
    while truncado and continuacoes < MAX_CONTINUACOES:
        continuacoes += 1
        print(
            f"[resumo] resposta truncada — pedindo continuação "
            f"({continuacoes}/{MAX_CONTINUACOES})..."
        )
        prompt_cont = _montar_prompt_continuacao(prompt_usuario, texto)
        parte, truncado = _chamar_provedor(prompt_cont)
        # Remove espaços duplicados na junção; deixa o LLM lidar com o resto.
        texto = texto.rstrip() + parte.lstrip()

    if truncado:
        print(
            f"[resumo] ainda truncado após {MAX_CONTINUACOES} continuações — "
            "seguindo com o que tem, validador decide se aceita."
        )

    return texto


def gerar_resumo(artigos):
    """
    Gera o digest markdown a partir da lista de artigos.

    Retorna uma string em markdown. Levanta exceção se a API falhar
    ou se o provedor configurado for inválido.
    """
    if not artigos:
        raise ValueError("Lista de artigos vazia — nada para resumir.")

    lista_formatada = _formatar_artigos(artigos)
    prompt_usuario = INSTRUCOES.format(lista_formatada=lista_formatada)

    print(f"[resumo] enviando {len(artigos)} artigo(s) ao LLM...")
    prompt_atual = prompt_usuario
    ultimo_erro = None
    for tentativa in range(1, MAX_TENTATIVAS_RESUMO + 1):
        resumo = _gerar_texto_base(prompt_atual)

        # Integridade: barra resumos truncados/incompletos antes mesmo de
        # checar cobertura. Se falhar aqui, não vale retry de "cobrir mais" —
        # é outro tipo de problema.
        erros_integridade = _validar_integridade(resumo)
        if erros_integridade:
            msg = "; ".join(erros_integridade)
            print(f"[resumo] falha de integridade na tentativa {tentativa}: {msg}")
            if tentativa < MAX_TENTATIVAS_RESUMO:
                # Nova tentativa completa — reinicia sem anexar correção de cobertura.
                prompt_atual = prompt_usuario
                continue
            raise DigestIncompletoError(
                f"Digest incompleto após {MAX_TENTATIVAS_RESUMO} tentativas: {msg}"
            )

        resumo = _mesclar_secoes_duplicadas(resumo)

        erros = _validar_resumo(resumo, len(artigos))
        if not erros:
            return resumo

        ultimo_erro = "; ".join(erros)
        print(f"[resumo] saída rejeitada na tentativa {tentativa}: {ultimo_erro}")
        if tentativa < MAX_TENTATIVAS_RESUMO:
            prompt_atual = prompt_usuario + PROMPT_CORRECAO_COBERTURA

    raise RuntimeError(f"Resumo rejeitado por cobertura insuficiente: {ultimo_erro}")


if __name__ == "__main__":
    from collector import coletar_artigos

    artigos = coletar_artigos()
    if not artigos:
        print("[teste] nenhum artigo coletado — abortando.")
    else:
        resumo = gerar_resumo(artigos)
        print("\n" + "=" * 60)
        print("RESUMO GERADO:")
        print("=" * 60)
        print(resumo)
