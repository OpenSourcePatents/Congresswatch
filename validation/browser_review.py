"""Exercise actual static member panels on desktop and mobile; no deployment."""
import functools
import http.server
import json
import os
from pathlib import Path
import threading

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/".cache/browser-review"
OUT.mkdir(parents=True, exist_ok=True)


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


server = http.server.ThreadingHTTPServer(("127.0.0.1", 0),
    functools.partial(QuietHandler, directory=str(ROOT)))
threading.Thread(target=server.serve_forever, daemon=True).start()
members = json.loads((ROOT/"data/members.json").read_text())
errors, checks = [], []
try:
    with sync_playwright() as p:
        launch = {"headless": True}
        if os.path.isfile("/usr/bin/chromium"):
            launch["executable_path"] = "/usr/bin/chromium"
        browser = p.chromium.launch(**launch)
        for width, height in ((1365, 900), (390, 844)):
            page = browser.new_page(viewport={"width": width, "height": height})
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{server.server_port}", wait_until="domcontentloaded")
            page.wait_for_function("typeof allMembers !== 'undefined' && allMembers.length > 500")
            page.screenshot(path=str(OUT/f"roster-{width}.png"), full_page=False)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
            for bid in ("B001260", "B001309", "M001169", "G000593"):
                page.evaluate("(id) => openProfile(allMembers.find(m => m.id === id))", bid)
                page.wait_for_function("(id) => currentMember.id === id && detailCache.has(id)", arg=bid)
                for panel in ("overview", "votes", "finance", "stocks", "travel", "patterns", "network"):
                    page.locator(f'.ptab[data-panel="{panel}"]').click()
                    assert page.locator(f"#ppanel-{panel}").is_visible()
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                checks.append({"width": width, "member": bid, "panels": 7})
                if bid in ("B001260", "M001169", "G000593"):
                    panel = {"B001260": "votes", "M001169": "stocks", "G000593": "travel"}[bid]
                    page.locator(f'.ptab[data-panel="{panel}"]').click()
                    page.screenshot(path=str(OUT/f"{bid}-{width}.png"), full_page=True)
            if width == 1365:
                # Render every real member with its actual detail record in the actual
                # browser runtime, including all heterogeneous data shapes.
                for member in members:
                    detail = json.loads((ROOT/f"data/details/{member['id']}.json").read_text())
                    page.evaluate("""({member,detail}) => {
                        const m = {...mapMember(member), ...detail};
                        const s = computeScore(m), salary = computeCareerEarnings(m) || 0;
                        renderOverview(m,s,salary); fillVotes(m,s); fillFinance(m,s,salary);
                        fillStocks(m,s); fillTravel(m); fillPatterns(m,s); fillDonors(m);
                        if (m.score_components.wealth_gap !== 0) throw new Error('wealth proxy');
                        if (eligibleSecSignals(m) && m.edgar_identity_status !== 'verified')
                            throw new Error('unverified SEC attribution');
                    }""", {"member": member, "detail": detail})
            page.close()
        browser.close()
finally:
    server.shutdown()
assert not errors, errors
report = {"viewports": [[1365, 900], [390, 844]], "profile_panel_checks": checks,
          "all_member_panel_renders": len(members), "javascript_errors": errors,
          "horizontal_document_overflow": False}
(OUT/"report.json").write_text(json.dumps(report, indent=2)+"\n")
print(json.dumps(report))
