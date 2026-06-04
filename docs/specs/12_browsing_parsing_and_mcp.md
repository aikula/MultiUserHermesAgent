# 12. Browsing, Parsing and MCP Specification

## Цель

Добавить агенту controlled browsing and parsing для учебного MVP:

- поиск информации;
- открытие страниц;
- извлечение текста;
- сбор ссылок;
- скачивание файлов по ссылкам;
- обработка скачанных файлов;
- optional browser automation для динамических сайтов.

Прямое управление компьютером исключено. Браузинг должен работать в controlled backend tools, а не через доступ к пользовательскому рабочему столу.

## Recommended approach

### Phase 1: SearxNG + HTTP fetch + parser

Use first because it is stable, simpler and enough for most учебных сценариев.

Tools:

- `web_search(query, limit)` via SearxNG;
- `fetch_url(url)`;
- `extract_readable_text(url)`;
- `extract_links(url, filters)`;
- `download_files(urls, allowed_types, max_count)`;
- `summarize_sources(sources)`.

### Phase 2: Playwright MCP or browser service

Use only for dynamic websites that require rendering JavaScript.

Tools:

- open page;
- get page text;
- screenshot optional later;
- click/fill optional later;
- extract links from rendered DOM.

For this sprint, Playwright is optional. Do not block demo on it.

## Existing ecosystem notes

Hermes upstream has optional tool ideas around web search and browser use, including Firecrawl and cloud browser integrations in its tool gateway ecosystem. Use built-in Hermes capabilities where easier, but keep our webapp-side tools as stable fallback for training demo.

For parsing sites, ready-made open-source options to evaluate:

- SearxNG for metasearch;
- trafilatura for extracting clean article text;
- readability-lxml for readable text extraction;
- BeautifulSoup for simple parsing;
- Playwright for dynamic sites;
- Firecrawl if API/service is acceptable;
- Crawl4AI if a crawler-style local service is preferred.

Default recommendation for demo:

1. SearxNG search.
2. HTTP fetch with timeout and size limit.
3. trafilatura/readability extraction.
4. download allowed files into user's Files area.
5. Optional Playwright adapter later.

## Backend module

Add `webapp/app/tools/web_tools.py`.

Functions:

- `search_web(uid, query, limit=10)`;
- `fetch_url(uid, url)`;
- `extract_links(uid, url, pattern=None, allowed_domains=None)`;
- `download_files(uid, urls, target_folder, max_count=10)`;
- `parse_html_to_text(html, url=None)`;
- `summarize_sources(uid, sources)` optional via Hermes.

## Config

Env variables:

- `SEARXNG_URL`;
- `WEB_FETCH_TIMEOUT_SECONDS=20`;
- `WEB_FETCH_MAX_BYTES=5000000`;
- `WEB_DOWNLOAD_MAX_FILES=10`;
- `WEB_DOWNLOAD_ALLOWED_EXTENSIONS=.pdf,.txt,.md,.csv,.json,.docx,.xlsx`;
- `WEB_ALLOWED_DOMAINS` optional;
- `WEB_BLOCK_PRIVATE_IPS=true`.

## Security and demo safety

Must have:

- block local/private IP ranges by default;
- block file:// and internal schemes;
- allow only http/https;
- max response size;
- max files per operation;
- file extension allowlist;
- save downloads under user files directory;
- no arbitrary shell commands.

Do not build a general crawler first. Build a controlled fetcher. The internet is already a landfill; no need to import it wholesale into the demo.

## Agent integration

Add manager/search skill prompt:

User commands:

- `Найди 10 источников по теме ... и сделай таблицу.`
- `Открой эти ссылки и выдели ключевые тезисы.`
- `Скачай до 10 PDF по этим ссылкам и сделай краткую сводку.`
- `Собери сравнительную таблицу конкурентов.`

External web fetch does not need approval for safe search/fetch, but bulk download should create approval intent:

- action_type: `web_download_files`;
- payload: urls, target_folder, max_count.

## UI

Can be minimal in this sprint:

- no separate browser UI required;
- results displayed in chat;
- downloaded files visible in Files tab.

Optional later:

- `Исследования` tab with saved search sessions.

## Acceptance checklist

- [ ] Agent can search web through SearxNG.
- [ ] Agent can fetch and parse one URL.
- [ ] Agent can extract links from one URL.
- [ ] Agent can download up to 10 allowed files into Files tab.
- [ ] Agent refuses blocked schemes and private IPs.
- [ ] Downloaded files are scoped to current user.
- [ ] Search result includes source URL/title/snippet.
- [ ] Parsing result includes source attribution.
- [ ] Large pages are capped.
- [ ] Dynamic browser automation is optional and not required for demo pass.

## Tests

- `test_search_web_calls_searxng`
- `test_fetch_url_rejects_file_scheme`
- `test_fetch_url_rejects_private_ip_when_enabled`
- `test_fetch_url_limits_size`
- `test_extract_links_filters_by_extension`
- `test_download_files_respects_max_count`
- `test_download_files_saves_to_user_folder`
- `test_download_files_rejects_unsafe_extension`
- `test_parse_html_to_text_returns_clean_text`

## Demo script

1. Ask: `Найди 5 источников про AI agents for managers`.
2. Agent returns table with title, URL, short note.
3. Ask: `Открой первые 3 источника и сделай executive summary`.
4. Ask: `Скачай PDF по найденным ссылкам, не больше 10`.
5. Files appear in `Файлы` tab.
6. Ask: `Сделай сравнительную таблицу по скачанным материалам`.

## Playwright MCP recommendation

Add as optional adapter after Phase 1.

Use cases:

- site requires JavaScript rendering;
- links are loaded dynamically;
- page text is unavailable through HTTP fetch.

Do not use Playwright for simple pages. Starting every task with a browser is like commuting to the kitchen by helicopter.
