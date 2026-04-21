"""
Configuração do AI News Digest.

Define os feeds RSS monitorados e parâmetros globais de coleta.
"""

# Janela de coleta: só considera artigos publicados nas últimas N horas.
HORAS_JANELA = 24

# Feeds RSS. Formato: (nome_fonte, url, tier)
#
# Tier:
#   "primario"   — lab, research oficial, analista de fonte primária,
#                  entrevistas/investigação originais. Vale mais como
#                  âncora de tema.
#   "secundario" — mídia tech, agregador, newsletter de resumo, análise
#                  sobre trabalho de terceiros. Ainda útil, mas cede
#                  espaço quando há primária cobrindo o mesmo assunto.
#
# Os artigos de fonte primária são ordenados antes dos secundários
# quando enviados ao LLM — isso enviesa a curadoria natural sem precisar
# anotar tier dentro de cada bloco.
FEEDS = [
    # --- Labs (fonte primária: releases, research) ---
    ("OpenAI", "https://openai.com/news/rss.xml", "primario"),
    ("Anthropic News", "https://raw.githubusercontent.com/Olshansk/rss-feeds/main/feeds/feed_anthropic_news.xml", "primario"),
    ("Anthropic Engineering", "https://raw.githubusercontent.com/Olshansk/rss-feeds/main/feeds/feed_anthropic_engineering.xml", "primario"),
    ("Google DeepMind", "https://deepmind.google/blog/rss.xml", "primario"),
    ("Google AI Blog", "https://blog.google/technology/ai/rss/", "primario"),
    ("Google Research", "https://research.google/blog/rss/", "primario"),
    ("Microsoft AI", "https://blogs.microsoft.com/ai/feed/", "primario"),
    ("Microsoft Research", "https://www.microsoft.com/en-us/research/feed/", "primario"),
    ("BAIR (Berkeley)", "https://bair.berkeley.edu/blog/feed.xml", "primario"),

    # --- Conteúdo técnico / prático ---
    ("Hugging Face Blog", "https://huggingface.co/blog/feed.xml", "primario"),
    ("Towards Data Science", "https://towardsdatascience.com/feed", "secundario"),
    ("KDnuggets", "https://www.kdnuggets.com/feed", "secundario"),
    ("MarkTechPost", "https://www.marktechpost.com/feed/", "secundario"),

    # --- Mídia tech (análise, contexto de mercado) ---
    ("The Verge (AI)", "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml", "secundario"),
    ("MIT Tech Review (AI)", "https://www.technologyreview.com/topic/artificial-intelligence/feed/", "secundario"),
    ("MIT News (AI)", "https://news.mit.edu/topic/mitartificial-intelligence2-rss.xml", "primario"),
    ("Wired (AI)", "https://www.wired.com/feed/tag/ai/latest/rss", "secundario"),
    ("Ars Technica (AI)", "https://arstechnica.com/ai/feed/", "secundario"),
    ("TechCrunch (AI)", "https://techcrunch.com/category/artificial-intelligence/feed/", "secundario"),
    ("VentureBeat (AI)", "https://venturebeat.com/category/ai/feed/", "secundario"),
    ("AI Business", "https://aibusiness.com/rss.xml", "secundario"),

    # --- Newsletters (curadoria humana) ---
    ("Import AI", "https://importai.substack.com/feed", "primario"),
    ("One Useful Thing", "https://www.oneusefulthing.org/feed", "secundario"),
    ("TLDR AI", "https://tldr.tech/api/rss/ai", "secundario"),

    # --- Análise estratégica (nível elite) ---
    ("Stratechery", "https://stratechery.com/feed/", "primario"),
    ("Latent Space", "https://www.latent.space/feed", "primario"),
    ("SemiAnalysis", "https://semianalysis.com/feed/", "primario"),
]
