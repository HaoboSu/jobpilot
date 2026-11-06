"""Job sources: LinkedIn public guest API, manual import, and labelled demo data.

The LinkedIn guest endpoint is the same one that powers public job-search pages;
it needs no login. Requests are best-effort: any failure is reported honestly in
the monitor log and never silently presented as real LinkedIn results.
"""
from __future__ import annotations

import copy
import hashlib
import html as html_lib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from .seed import DEMO_JOBS

SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
DETAIL_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# ------------------------------------------------------- region / location
_EUROPE_TERMS = [
    "欧洲", "欧盟", "Europe", "European",
    "德国", "英国", "法国", "荷兰", "瑞士", "匈牙利", "波兰", "罗马尼亚", "保加利亚",
    "葡萄牙", "西班牙", "意大利", "奥地利", "比利时", "瑞典", "丹麦", "挪威", "芬兰",
    "爱尔兰", "捷克", "斯洛伐克", "希腊", "克罗地亚", "塞尔维亚", "卢森堡", "冰岛",
    "斯洛文尼亚", "爱沙尼亚", "拉脱维亚", "立陶宛", "马耳他", "塞浦路斯", "乌克兰",
    "Germany", "United Kingdom", "UK", "France", "Netherlands", "Switzerland",
    "Hungary", "Poland", "Romania", "Bulgaria", "Portugal", "Spain", "Italy",
    "Austria", "Belgium", "Sweden", "Denmark", "Norway", "Finland", "Ireland",
    "Czech", "Slovakia", "Greece", "Croatia", "Serbia", "Luxembourg", "Iceland",
    "Slovenia", "Estonia", "Latvia", "Lithuania", "Malta", "Cyprus", "Ukraine",
    "里斯本", "苏菲亚", "华沙", "阿姆斯特丹", "巴黎", "柏林", "苏黎世", "日内瓦",
    "巴塞尔", "伯尔尼", "维也纳", "布达佩斯", "慕尼黑", "都柏林", "斯德哥尔摩",
    "哥本哈根", "布鲁塞尔", "马德里", "罗马", "米兰", "巴塞罗那", "布拉格",
    "奥斯陆", "赫尔辛基", "利物浦", "曼彻斯特", "伦敦", "爱丁堡", "汉堡", "法兰克福",
    "Lisbon", "Sofia", "Warsaw", "Amsterdam", "Paris", "Berlin", "Zurich", "Geneva",
    "Basel", "Bern", "Vienna", "Budapest", "Munich", "Dublin", "Stockholm",
    "Copenhagen", "Brussels", "Madrid", "Rome", "Milan", "Barcelona", "Prague",
    "Oslo", "Helsinki", "Liverpool", "Manchester", "London", "Edinburgh", "Hamburg",
]

# Chinese/alias -> (LinkedIn location query, terms used to recognise a match)
REGION_MAP: dict[str, tuple[str, list[str]]] = {
    "欧洲": ("Europe", _EUROPE_TERMS),
    "欧盟": ("Europe", _EUROPE_TERMS),
    "瑞士": ("Switzerland", ["瑞士", "Switzerland", "苏黎世", "日内瓦", "巴塞尔", "伯尔尼",
                             "Zurich", "Geneva", "Basel", "Bern"]),
    "匈牙利": ("Hungary", ["匈牙利", "Hungary", "布达佩斯", "Budapest"]),
    "德国": ("Germany", ["德国", "Germany", "柏林", "慕尼黑", "汉堡", "法兰克福",
                         "Berlin", "Munich", "Hamburg"]),
    "英国": ("United Kingdom", ["英国", "United Kingdom", "英格兰", "苏格兰", "伦敦",
                                "London", "England", "Manchester", "Liverpool"]),
    "荷兰": ("Netherlands", ["荷兰", "Netherlands", "阿姆斯特丹", "Amsterdam", "Rotterdam"]),
    "法国": ("France", ["法国", "France", "巴黎", "Paris", "Lyon"]),
    "美国": ("United States", ["美国", "United States", "USA", "U.S."]),
    "中国": ("China", ["中国", "China", "上海", "北京", "深圳", "广州", "杭州",
                        "Shanghai", "Beijing", "Shenzhen"]),
    "上海": ("Shanghai", ["上海", "Shanghai"]),
    "北京": ("Beijing", ["北京", "Beijing"]),
    "深圳": ("Shenzhen", ["深圳", "Shenzhen"]),
    "新加坡": ("Singapore", ["新加坡", "Singapore"]),
    "日本": ("Japan", ["日本", "Japan", "东京", "Tokyo"]),
    "加拿大": ("Canada", ["加拿大", "Canada", "多伦多", "温哥华", "Toronto", "Vancouver"]),
    "澳大利亚": ("Australia", ["澳大利亚", "Australia", "悉尼", "墨尔本", "Sydney", "Melbourne"]),
    "印度": ("India", ["印度", "India", "班加罗尔", "Bangalore"]),
    "远程": ("", ["Remote", "Work from home"]),
}

_US_MARKERS = ("美国", "united states", "usa", "u.s.")


def parse_location_queries(text: str) -> tuple[list[str], list[str]]:
    """Turn a Chinese / multi-region location string into LinkedIn queries + match terms.

    LinkedIn only understands one location per request and does not recognise
    Chinese place names, so each part is mapped to an English query and searched
    separately. The terms are reused later to score/filter returned jobs.
    """
    parts = [x.strip() for x in re.split(r"[,，、;；/|｜]+", text or "") if x.strip()]
    queries: list[str] = []
    terms: list[str] = []
    for part in parts:
        entry = None
        low = part.lower()
        for key, value in REGION_MAP.items():
            if low == key.lower() or key in part:
                entry = value
                break
        if entry is None:
            for key, value in REGION_MAP.items():
                if low in [t.lower() for t in value[1][:8]]:
                    entry = value
                    break
        if entry:
            query, aliases = entry
            if query and query not in queries:
                queries.append(query)
            terms.extend(aliases)
        else:
            if part not in queries:
                queries.append(part)
            terms.append(part)
    return queries, list(dict.fromkeys(t for t in terms if t))


def region_matches(location: str | None, terms: list[str]) -> bool:
    """Whether a job's location is inside the user's preferred regions."""
    low = (location or "").strip().lower()
    if not low:
        return True  # unknown location: do not silently discard
    if not terms:
        return True
    us_preferred = any(t.lower() in ("美国", "united states", "usa") for t in terms)
    if not us_preferred and any(marker in low for marker in _US_MARKERS):
        return False
    return any(t.lower() in low for t in terms if t)


_PROXY_CACHE: dict = {"ts": 0.0, "value": None}


def _windows_proxy_settings():
    """Read the Windows system proxy from the registry (not read by urllib)."""
    if sys.platform != "win32":
        return None, False, None
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        )
        try:
            enable, _ = winreg.QueryValueEx(key, "ProxyEnable")
        except OSError:
            enable = 0
        try:
            server, _ = winreg.QueryValueEx(key, "ProxyServer")
        except OSError:
            server = ""
        try:
            auto, _ = winreg.QueryValueEx(key, "AutoConfigURL")
        except OSError:
            auto = ""
        key.Close()
        return server or None, bool(enable), auto or None
    except Exception:  # noqa: BLE001 - registry access is best-effort
        return None, False, None


def _normalize_proxy(server: str | None) -> tuple[str | None, str]:
    if not server:
        return None, ""
    raw = server.strip()
    note = ""
    if ";" in raw:
        chosen = None
        for part in raw.split(";"):
            if "=" in part:
                proto, value = part.split("=", 1)
                if proto.strip().lower() in ("http", "https") and not chosen:
                    chosen = value.strip()
        raw = chosen or raw.split(";")[0].split("=")[-1]
    low = raw.lower()
    if low.startswith(("socks5", "socks4", "socks:")) or "=socks" in server.lower():
        return None, "检测到 SOCKS 代理，Python 标准库需要 HTTP 代理端口（如 Clash 的 7890）才能使用"
    if "://" not in raw:
        raw = "http://" + raw
    return raw.strip(), note


def detect_system_proxy(force: bool = False) -> dict:
    """Find the OS-level proxy that a VPN usually configures.

    Python's urllib only reads HTTP_PROXY/HTTPS_PROXY environment variables;
    it does NOT read the Windows system proxy from the registry. That is why a
    VPN can work in the browser while server-side requests still leave via the
    local ISP. We bridge that gap here.
    """
    now = time.time()
    if not force and _PROXY_CACHE["value"] and now - _PROXY_CACHE["ts"] < 30:
        return _PROXY_CACHE["value"]

    server, enabled, auto = _windows_proxy_settings()
    registry_proxy, note = _normalize_proxy(server) if enabled else (None, "")
    env_map = {}
    for key in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy",
                "ALL_PROXY", "all_proxy"):
        if os.environ.get(key):
            env_map[key] = os.environ[key]
    env_proxy = (env_map.get("HTTPS_PROXY") or env_map.get("https_proxy")
                 or env_map.get("HTTP_PROXY") or env_map.get("http_proxy") or None)
    recommended = registry_proxy or env_proxy
    if not note:
        if registry_proxy:
            note = "已检测到 Windows 系统代理（通常由 VPN 设置），应用会自动使用它抓取领英"
        elif env_proxy:
            note = "未检测到系统代理，将使用环境变量中的代理"
        else:
            note = "未检测到系统代理，将直连（受限地区通常返回 HTTP 451）"
    result = {
        "registry_enabled": enabled,
        "registry_server": server,
        "registry_proxy": registry_proxy,
        "auto_config_url": auto,
        "env_proxy": env_proxy,
        "env_all": env_map,
        "recommended": recommended,
        "note": note,
    }
    _PROXY_CACHE.update(ts=now, value=result)
    return result


def effective_proxy(settings: dict | None = None) -> str:
    """Proxy actually used for a fetch: explicit config > OS proxy > env proxy."""
    settings = settings or {}
    configured = str(settings.get("linkedin_proxy") or "").strip()
    if configured:
        return configured
    return detect_system_proxy().get("recommended") or ""


def mask_proxy(url: str) -> str:
    """Hide credentials if a proxy URL contains user:pass@."""
    if not url:
        return ""
    return re.sub(r"//([^/@]+)@", "//***@", url)


def _strip_tags(fragment: str) -> str:
    fragment = re.sub(r"<script\b[^>]*>.*?</script>", " ", fragment, flags=re.S | re.I)
    fragment = re.sub(r"<br\s*/?>|</p>|</div>|</li>|</h\d>", "\n", fragment, flags=re.I)
    fragment = re.sub(r"<[^>]+>", " ", fragment)
    return re.sub(r"[ \t]+", " ", html_lib.unescape(fragment)).strip()


def _clean_multiline(fragment: str) -> str:
    text = _strip_tags(fragment)
    lines = [ln.strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln).strip()


def _request(url: str, timeout: int = 12, settings: dict | None = None) -> str:
    settings = settings or {}
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": "https://www.linkedin.com/jobs/",
        },
    )
    proxy = effective_proxy(settings)
    if proxy:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        )
    else:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=timeout) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace")


def _card_field(pattern: str, block: str) -> str:
    m = re.search(pattern, block, flags=re.S | re.I)
    return _strip_tags(m.group(1)) if m else ""


def parse_search_cards(page: str) -> list[dict]:
    cards: list[dict] = []
    blocks = re.findall(r"<li\b.*?</li>", page, flags=re.S | re.I)
    if not blocks:
        blocks = re.split(r"(?=<li\b)", page)
    for block in blocks:
        if "base-search-card" not in block and "base-card__full-link" not in block:
            continue
        url = ""
        m = re.search(r'base-card__full-link[^>]*href="([^"]+)"', block, flags=re.I)
        if not m:
            m = re.search(r'href="(https://www\.linkedin\.com/jobs/view/[^"]+)"', block, flags=re.I)
        if m:
            url = html_lib.unescape(m.group(1)).split("?")[0]
        title = _card_field(r'class="[^"]*base-search-card__title[^"]*"[^>]*>(.*?)</', block)
        if not title:
            title = _card_field(r"<h3[^>]*>(.*?)</h3>", block)
        company = _card_field(r'class="[^"]*base-search-card__subtitle[^"]*"[^>]*>(.*?)</', block)
        if not company:
            company = _card_field(r"<h4[^>]*>(.*?)</h4>", block)
        location = _card_field(r'class="[^"]*job-search-card__location[^"]*"[^>]*>(.*?)</', block)
        posted = ""
        dm = re.search(r'datetime="([^"]+)"', block, flags=re.I)
        if dm:
            posted = dm.group(1)
        else:
            tm = re.search(r"<time[^>]*>(.*?)</time>", block, flags=re.S | re.I)
            if tm:
                posted = _strip_tags(tm.group(1))
        jid = ""
        im = re.search(r"/jobs/view/(\d+)", url)
        if im:
            jid = im.group(1)
        else:
            # Modern LinkedIn links use a slug with the numeric id at the end:
            # /jobs/view/senior-data-analyst-at-acme-4485915276
            ids = re.findall(r"\d{6,}", url)
            if ids:
                jid = ids[-1]
        if not title:
            continue
        cards.append({
            "external_id": f"li-{jid}" if jid else "li-" + hashlib.md5(
                (title + company + url).encode("utf-8")).hexdigest()[:12],
            "job_id": jid,
            "source": "linkedin",
            "title": title,
            "company": company,
            "location": location,
            "url": url,
            "posted_at": posted[:10] if posted else "",
            "description": "",
            "remote": bool(re.search(r"remote|远程", (location + block), re.I)),
        })
    return cards


def parse_job_detail(page: str) -> tuple[str, str]:
    """Return (description, employment_type)."""
    desc = ""
    for pattern in (
        r'class="[^"]*show-more-less-html__markup[^"]*"[^>]*>(.*?)</div>',
        r'class="[^"]*description__text[^"]*"[^>]*>(.*?)</div>',
        r'class="[^"]*job-view-section[^"]*"[^>]*>(.*?)</section>',
    ):
        m = re.search(pattern, page, flags=re.S | re.I)
        if m:
            desc = _clean_multiline(m.group(1))
            if len(desc) > 80:
                break
    if not desc:
        # fallback: the plain-text criteria section, still real JD content
        m = re.search(r'class="[^"]*description__text[^"]*"[^>]*>(.*)', page, flags=re.S | re.I)
        if m:
            desc = _clean_multiline(m.group(1)[:12000])
    emp = ""
    em = re.search(r'class="[^"]*job-posting-benefits[^"]*"[^>]*>(.*?)</', page, flags=re.S | re.I)
    if em:
        emp = _strip_tags(em.group(1))[:60]
    return desc, emp


def fetch_linkedin(settings: dict) -> tuple[list[dict], list[str]]:
    """Fetch real public LinkedIn job postings. Returns (jobs, notes)."""
    notes: list[str] = []
    proxy_used = effective_proxy(settings)
    notes.append("抓取通道：" + (mask_proxy(proxy_used) if proxy_used else "直连（未检测到代理）"))
    keywords = [k.strip() for k in (settings.get("query_keywords") or []) if k.strip()]
    if not keywords:
        keywords = [k.strip() for k in re.split(r"[,，]", settings.get("keywords", "")) if k.strip()]
    keywords = keywords[:4] or ["Data Analyst"]
    location = (settings.get("location") or "").strip()
    location_queries, region_terms = parse_location_queries(location)
    notes.append(
        "地点解析："
        + (" / ".join(location_queries) if location_queries else "不限地点")
        + (f"（{len(region_terms)} 个地区匹配词）" if region_terms else "")
    )
    max_results = max(5, min(60, int(settings.get("max_results") or 30)))
    time_range = settings.get("time_range") or "r604800"
    max_queries = 8

    collected: dict[str, dict] = {}
    query_count = 0
    for kw in keywords:
        for loc in (location_queries or [""]):
            if query_count >= max_queries or len(collected) >= max_results:
                break
            query_count += 1
            label = f"「{kw}」@ {loc or '不限地点'}"
            params = {"keywords": kw, "location": loc, "start": 0, "f_TPR": time_range}
            if settings.get("remote_only"):
                params["f_WT"] = "2"
            url = SEARCH_URL + "?" + urllib.parse.urlencode(params)
            try:
                page = _request(url, settings=settings)
            except urllib.error.HTTPError as exc:
                hint = ("（地区或法律限制，当前网络无法访问领英；可配置代理后重试）"
                        if exc.code in (451, 403) else "")
                notes.append(f"领英搜索 {label} 返回 HTTP {exc.code}{hint}，已跳过")
                time.sleep(0.4)
                continue
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                notes.append(f"领英搜索 {label} 连接失败：{type(exc).__name__}")
                time.sleep(0.4)
                continue
            cards = parse_search_cards(page)
            notes.append(f"领英搜索 {label} 取得 {len(cards)} 条职位卡片")
            for card in cards:
                collected.setdefault(card["external_id"], card)
            time.sleep(0.5)
        if query_count >= max_queries or len(collected) >= max_results:
            break

    jobs = list(collected.values())[:max_results]
    if settings.get("region_mode") == "strict" and region_terms:
        kept = [j for j in jobs if region_matches(j.get("location"), region_terms)]
        notes.append(f"地区严格过滤：保留 {len(kept)}/{len(jobs)} 个符合首选地区的职位")
        jobs = kept
    # Fetch JD detail for the top slice; keep card text as a real fallback.
    detail_ok = 0
    for job in jobs[:20]:
        if not job.get("job_id"):
            continue
        try:
            page = _request(DETAIL_URL.format(job_id=job["job_id"]), settings=settings)
            desc, emp = parse_job_detail(page)
            if desc:
                job["description"] = desc
                detail_ok += 1
            if emp:
                job["employment_type"] = emp
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
            pass
        time.sleep(0.4)
    if jobs:
        notes.append(f"成功获取 {detail_ok}/{min(len(jobs), 20)} 个岗位的职位描述")
    if jobs:
        for job in jobs:
            if not job["description"]:
                job["description"] = (
                    f"职位名称：{job['title']}；公司：{job['company']}；"
                    f"地点：{job['location']}。（领英详情接口未返回完整 JD，"
                    "此处仅保留公开卡片信息，匹配度可能偏低）"
                )
    return jobs, notes


def demo_jobs() -> list[dict]:
    jobs = copy.deepcopy(DEMO_JOBS)
    for j in jobs:
        j["_demo_label"] = "演示数据（非真实招聘信息）"
    return jobs


def normalize_manual_jobs(items: list, settings: dict | None = None) -> list[dict]:
    """Normalise user-pasted / imported job postings."""
    if isinstance(items, dict):
        items = items.get("jobs") or [items]
    out: list[dict] = []
    for raw in items or []:
        if isinstance(raw, str):
            raw = {"description": raw}
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or raw.get("jobTitle") or "").strip()
        desc = str(raw.get("description") or raw.get("jd") or raw.get("text") or "").strip()
        if not title and not desc:
            continue
        if not title:
            first = next((ln.strip() for ln in desc.splitlines() if ln.strip()), "")
            title = first[:60]
        company = str(raw.get("company") or "").strip()
        url = str(raw.get("url") or raw.get("link") or "").strip()
        seed = "|".join((title, company, url, desc[:120]))
        out.append({
            "external_id": "manual-" + hashlib.md5(seed.encode("utf-8")).hexdigest()[:14],
            "source": "manual",
            "title": title,
            "company": company or "未提供公司名",
            "location": str(raw.get("location") or "").strip(),
            "url": url,
            "description": desc,
            "posted_at": str(raw.get("posted_at") or "").strip()[:10],
            "employment_type": str(raw.get("employment_type") or "").strip(),
            "remote": bool(raw.get("remote")),
            "salary_text": str(raw.get("salary_text") or "").strip(),
        })
    return out


def parse_manual_payload(text: str) -> list[dict]:
    """Accept a JSON array/object, or plain pasted JD text."""
    text = (text or "").strip()
    if not text:
        return []
    if text.startswith("[") or text.startswith("{"):
        try:
            data = json.loads(text)
            return normalize_manual_jobs(data if isinstance(data, list) else [data])
        except (ValueError, TypeError):
            pass
    return normalize_manual_jobs([{"description": text}])


def _probe_channel(proxy: str, timeout: int = 9) -> dict:
    handlers = (urllib.request.ProxyHandler({"http": proxy, "https": proxy})
                if proxy else urllib.request.ProxyHandler({}))
    opener = urllib.request.build_opener(handlers)
    result = {
        "proxy": mask_proxy(proxy) or "直连",
        "linkedin": "", "exit_ip": "", "exit_country": "", "exit_city": "",
    }
    req = urllib.request.Request(
        SEARCH_URL + "?keywords=Data%20Analyst&start=0",
        headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
    )
    try:
        with opener.open(req, timeout=timeout) as resp:
            page = resp.read().decode("utf-8", errors="replace")
        result["linkedin"] = f"HTTP 200 · 解析到 {len(parse_search_cards(page))} 个职位卡片"
    except urllib.error.HTTPError as exc:
        result["linkedin"] = f"HTTP {exc.code} {exc.reason}"
    except Exception as exc:  # noqa: BLE001
        result["linkedin"] = f"{type(exc).__name__}: {str(exc)[:90]}"
    try:
        with opener.open("https://ipinfo.io/json", timeout=timeout) as resp:
            info = json.loads(resp.read().decode("utf-8", errors="replace"))
        result["exit_ip"] = info.get("ip", "")
        result["exit_country"] = info.get("country", "")
        result["exit_city"] = info.get("city", "")
    except Exception as exc:  # noqa: BLE001
        result["exit_ip"] = f"无法获取（{type(exc).__name__}）"
    return result


def probe_network(settings: dict | None = None) -> dict:
    """Diagnose why LinkedIn may be unreachable, with real measured results."""
    settings = settings or {}
    detected = detect_system_proxy(force=True)
    configured = str(settings.get("linkedin_proxy") or "").strip()
    active = effective_proxy(settings)
    direct = _probe_channel("")
    via = _probe_channel(active) if active else None
    ok = bool(via and via["linkedin"].startswith("HTTP 200")) or direct["linkedin"].startswith("HTTP 200")
    if ok:
        advice = "当前通道可以正常抓取领英，点击「立即运行监控」即可获取真实职位。"
    elif detected.get("registry_proxy"):
        advice = ("检测到系统代理但抓取仍失败：请确认 VPN 客户端已连接、节点可用，"
                  "且该端口是 HTTP 代理（非纯 SOCKS）。可在下方手动指定端口后重试。")
    else:
        advice = ("未检测到系统代理，服务端请求从本地网络直连。请开启 VPN 的「系统代理 / 全局模式」，"
                  "或在下方填写 VPN 的本地 HTTP 代理地址（常见为 127.0.0.1:7890）。")
    return {
        "detected": detected,
        "configured_proxy": configured,
        "active_proxy": active,
        "direct": direct,
        "active_channel": via,
        "ok": ok,
        "advice": advice,
    }


def source_labels() -> dict[str, str]:
    return {"linkedin": "领英（真实公开职位）", "manual": "手动导入", "demo": "演示数据"}
