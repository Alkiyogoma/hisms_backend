"""
Email viewer - shows all emails sent by the system, rendered from the
template HTML that was stored by the JSON email backend.
Access at /emails/
"""
import json, html as html_lib
from pathlib import Path
from django.http import HttpResponse

EMAIL_LOG = Path(__file__).resolve().parent.parent / "sent_emails.json"


def email_viewer(request):
    try:
        data = json.loads(EMAIL_LOG.read_text(encoding="utf-8")) if EMAIL_LOG.exists() else []
    except Exception:
        data = []

    data = list(reversed(data))
    show = request.GET.get("show", "render")
    if show not in ("render", "raw"):
        show = "render"

    page = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Sent Emails ({len(data)})</title>
<style>
body {{ font-family: -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif; background: #1a1a2e; color: #e0e0e0; margin: 20px; }}
h1 {{ color: #00d4ff; }}
.toolbar {{ margin-bottom: 18px; font-size: 14px; }}
.toolbar a {{ color: #00d4ff; margin-right: 14px; }}
.email {{ border: 1px solid #333; border-radius: 8px; margin: 14px 0; background: #16213e; overflow: hidden; }}
.email-head {{ padding: 12px 15px; }}
.meta {{ color: #888; font-size: 12px; margin-bottom: 6px; }}
.meta span {{ margin-right: 20px; }}
.subject {{ color: #00d4ff; font-size: 16px; font-weight: bold; margin-bottom: 4px; }}
.to {{ color: #ffd700; }}
.actions {{ margin: 6px 0 0; }}
.actions a {{ color: #00d4ff; text-decoration: none; font-size: 12px; margin-right: 15px; }}
.frame-wrap {{ margin: 0 15px 15px; border: 1px solid #444; border-radius: 6px; background: #fff; }}
.frame-wrap iframe {{ width: 100%; border: 0; display: block; min-height: 420px; background: #fff; }}
.raw {{ background: #0f3460; padding: 10px; border-radius: 4px; white-space: pre-wrap; margin: 0 15px 15px; max-height: 420px; overflow-y: auto; font-family: monospace; font-size: 12px; }}
.plain {{ background: #0f3460; padding: 10px; border-radius: 4px; white-space: pre-wrap; margin: 0 15px 15px; max-height: 160px; overflow-y: auto; font-family: monospace; font-size: 12px; }}
.toggle {{ cursor: pointer; color: #00d4ff; text-decoration: underline; font-size: 12px; margin: 0 0 12px 15px; }}
</style></head><body>
<h1>Sent Emails</h1>
<div class="toolbar">
  <a href="/admissions/">Back to Pipeline</a>
  <a href="/emails/?show=render">Refresh</a>
  <a href="/emails/?show=raw">Raw view</a>
</div>
"""
    if not data:
        page += "<p>No emails sent yet.</p>"

    for i, e in enumerate(data):
        subject = html_lib.escape(e.get("subject", "(no subject)") or "")
        from_ = html_lib.escape(e.get("from", "") or "")
        to = html_lib.escape(", ".join(e.get("to", []) or []))
        has_html = bool(e.get("html"))
        page += f"""<div class="email">
<div class="email-head">
<div class="meta"><span>#{i+1}</span><span>From: {from_}</span><span id="len{i}"></span></div>
<div class="subject">{subject}</div>
<div class="to">To: {to}</div>
<div class="actions">
  <a href="/emails/?show=render#e{i}">top</a>
  <a href="/emails/?show=raw#e{i}">raw</a>
</div>
</div><a name="e{i}"></a>
"""
        if show == "raw" or not has_html:
            body = e.get("body", "")
            page += f"""<div class="raw">{html_lib.escape(body or "")}</div>"""
        else:
            frame_html = e.get("html") or ""
            # Embed via srcdoc; escaped quote=True keeps the attribute intact.
            srcdoc = html_lib.escape(frame_html, quote=True)
            page += f"""<div class="frame-wrap"><iframe id="f{i}" srcdoc="{srcdoc}" loading="lazy"></iframe></div>"""
            page += f"""<div class="plain"><b>Plain text body:</b>\n{html_lib.escape(e.get("body", "") or "")}</div>"""

        page += """<script>
  (function(){
    var f = document.getElementById('f%d');
    if (!f) return;
    function size(){ try { if (f.contentWindow && f.contentWindow.document && f.contentWindow.document.body) { f.style.height = Math.max(420, f.contentWindow.document.body.scrollHeight + 24) + 'px'; } } catch(e) {} }
    f.addEventListener('load', function(){ size(); setTimeout(size, 250); setTimeout(size, 1200); });
    if (!f.getAttribute('srcdoc')) { size(); }
  })();
</script>
""" % i

        page += "</div>"

    page += "</body></html>"
    return HttpResponse(page)