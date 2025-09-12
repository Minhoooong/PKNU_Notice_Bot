from .base import SiteAdapter
from urllib.parse import urljoin
from bs4 import BeautifulSoup
import hashlib

class PKNUAI2025(SiteAdapter):
    async def _build_detail_url(self, a_handle):
        sel = self.sel
        base = sel.get("site", "base_url")
        path = sel.get("detail", "url_path")
        fixed = sel.get("detail", "params_fixed")
        ordered_keys = sel.get("detail", "params_from_data_attrs_ordered")

        # 1) 고정 파라미터 (mId, order)
        fixed_qs = "&".join([f"{k}={v}" for k, v in fixed.items()])

        # 2) data-* 파라미터 (순서 보존 필수)
        ordered_pairs = []
        for k in ordered_keys:
            data_attr = k.replace("_", "-")  # data-page-index
            v = await a_handle.get_attribute(f"data-{data_attr}")
            ordered_pairs.append((k, v or ""))

        dyn_qs = "&".join([f"{k}={v}" for k, v in ordered_pairs])
        return urljoin(base, path) + "?" + fixed_qs + "&" + dyn_qs

    async def _parse_current_list(self, page):
        sel = self.sel
        html = await page.content()
        soup = BeautifulSoup(html, "html.parser")

        box_css  = sel.get("nonSbjt", "list", "container")
        item_css = sel.get("nonSbjt", "list", "item")
        anchor   = sel.get("nonSbjt", "list", "anchor")
        titlecss = sel.get("nonSbjt", "list", "title")
        datecss  = sel.get("nonSbjt", "list", "date")
        statcss  = sel.get("nonSbjt", "list", "status")

        box = soup.select_one(box_css) or soup
        items = box.select(item_css)
        results = []

        li_handles = await page.query_selector_all(item_css)
        # header/utility 행은 anchor 없음 → 자동 스크립
        for idx, li in enumerate(li_handles):
            a = await li.query_selector(anchor)
            if not a:
                continue

            detail_url = await self._build_detail_url(a)

            it = items[idx] if idx < len(items) else None
            def gtext(css):
                if not it: return ""
                n = it.select_one(css)
                return (n.get_text(strip=True) if n else "").strip()

            title  = gtext(titlecss)
            period = gtext(datecss)
            status = gtext(statcss)

            uid = hashlib.sha1(detail_url.encode()).hexdigest()[:16]
            results.append({
                "id": uid,
                "title": title,
                "period": " ".join(period.split()),
                "status": status,
                "url": detail_url
            })

        return results

    async def _goto_list(self):
        base = self.sel.get("site", "base_url")
        target = self.sel.get("site", "target_url")
        await self.page.goto(urljoin(base, target))

    async def iter_current(self):
        """현재 선택된 yy/shtm만 수집"""
        await self._goto_list()
        rows = await self._parse_current_list(self.page)
        for r in rows:
            yield r

    async def iter_all_terms(self):
        """
yy × shtm 전 조합 수집(페이지네이션 없음)"""
        await self._goto_list()
        # 분류 커믵 해제(전체)
        try:
            await self.page.evaluate("""() => {
                const boxes = document.querySelectorAll('input[name=clsfChk]');
                boxes.forEach(b => { if (b.checked) b.click(); });
            }""")
        except Exception:
            pass

        yy_sel   = self.sel.get("nonSbjt","filters","yy_select")
        shtm_sel = self.sel.get("nonSbjt","filters","shtm_select")

        yy_vals   = await self.page.eval_on_selector_all(f"{yy_sel} option", "els => els.map(e=>e.value).filter(Boolean)")
        shtm_vals = await self.page.eval_on_selector_all(f"{shtm_sel} option", "els => els.map(e=>e.value).filter(Boolean)")

        for yy in yy_vals:
            await self.page.select_option(yy_sel, yy)
            for shtm in shtm_vals:
                await self.page.select_option(shtm_sel, shtm)
                await self.page.wait_for_timeout(500)
                rows = await self._parse_current_list(self.page)
                for r in rows:
                    r["yy"] = yy
                    r["shtm"] = shtm
                    yield r
