import math
import subprocess
import time
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from config_store import mm_to_in


class AppContext:
    def __init__(self, config_store, state_store, log_buffer) -> None:
        self.config_store = config_store
        self.state_store = state_store
        self.log_buffer = log_buffer


def _format_float(value: float) -> str:
    if value is None or not math.isfinite(value):
        return "-"
    return f"{value:.3f}"


def _body_class(page: str, state=None) -> str:
    classes = []
    if page:
        classes.append(page)
    if page in ("operator", "targets", "wifi"):
        classes.append("touch")
    if page == "operator":
        classes.append("pass" if state and state.overall_pass else "fail")
    return " ".join(classes)


def _run_cmd(args: List[str], timeout_s: float) -> Tuple[bool, str, str]:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout_s)
    except FileNotFoundError:
        return False, "", f"{args[0]} not found"
    except subprocess.TimeoutExpired:
        return False, "", "command timed out"
    if result.returncode != 0:
        return False, result.stdout.strip(), result.stderr.strip()
    return True, result.stdout.strip(), result.stderr.strip()


def _split_nmcli_fields(line: str) -> List[str]:
    parts: List[str] = []
    current: List[str] = []
    escape = False
    for ch in line:
        if escape:
            current.append(ch)
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == ":":
            parts.append("".join(current))
            current = []
            continue
        current.append(ch)
    parts.append("".join(current))
    return parts


def _get_wlan_ip() -> Optional[str]:
    ok, out, _ = _run_cmd(["ip", "-4", "-o", "addr", "show", "dev", "wlan0"], timeout_s=2.0)
    if not ok or not out:
        return None
    parts = out.split()
    for idx, token in enumerate(parts):
        if token == "inet" and idx + 1 < len(parts):
            return parts[idx + 1].split("/")[0]
    return None


def _get_active_connection_name() -> Optional[str]:
    ok, out, _ = _run_cmd(["nmcli", "-t", "-f", "GENERAL.CONNECTION", "dev", "show", "wlan0"], timeout_s=2.0)
    if not ok or not out:
        return None
    _, _, value = out.partition(":")
    value = value.strip()
    return value if value else None


def _list_wifi_networks(rescan: bool) -> Tuple[List[Dict[str, str]], Optional[str], Optional[str], Optional[str]]:
    args = ["nmcli", "-t", "-c", "no", "-f", "ACTIVE,SSID,SIGNAL,SECURITY", "dev", "wifi", "list"]
    if rescan:
        args += ["--rescan", "yes"]
    ok, out, err = _run_cmd(args, timeout_s=8.0)
    networks: List[Dict[str, str]] = []
    active_ssid = None
    if not ok:
        return networks, active_ssid, _get_wlan_ip(), err or out or "Failed to scan WiFi"
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = _split_nmcli_fields(line)
        if len(parts) < 4:
            continue
        active = parts[0].strip() in ("yes", "*")
        ssid = parts[1].strip() or "<hidden>"
        try:
            signal = int(parts[2].strip())
        except ValueError:
            signal = 0
        security = parts[3].strip() or "open"
        if active:
            active_ssid = ssid
        networks.append(
            {
                "ssid": ssid,
                "signal": signal,
                "security": security,
                "active": active,
            }
        )
    networks.sort(key=lambda n: (not n["active"], -int(n["signal"]), n["ssid"]))
    if not active_ssid:
        active_ssid = _get_active_connection_name()
    return networks, active_ssid, _get_wlan_ip(), None


def _connect_wifi(ssid: str, password: str) -> Tuple[bool, str]:
    if not ssid:
        return False, "SSID is required"
    args = ["nmcli", "dev", "wifi", "connect", ssid]
    if password:
        args += ["password", password]
    ok, out, err = _run_cmd(args, timeout_s=20.0)
    if ok:
        return True, out or f"Connected to {ssid}"
    return False, err or out or "Failed to connect"


def create_app(context: AppContext) -> FastAPI:
    app = FastAPI()
    app.state.context = context

    templates = Jinja2Templates(directory="templates")
    app.mount("/static", StaticFiles(directory="static"), name="static")

    @app.get("/")
    def root() -> RedirectResponse:
        return RedirectResponse(url="/operator", status_code=302)

    @app.get("/status", response_class=HTMLResponse)
    def status_page(request: Request):
        cfg = context.config_store.snapshot()
        state = context.state_store.snapshot()
        last_ok_ts = state.last_ok_ts
        last_ok_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(last_ok_ts)) if last_ok_ts else "-"
        last_ok_age = (time.time() - last_ok_ts) if last_ok_ts else None
        return templates.TemplateResponse(
            "status.html",
            {
                "request": request,
                "cfg": cfg,
                "state": state,
                "body_class": _body_class("dashboard", state),
                "now": time.time(),
                "last_ok_str": last_ok_str,
                "last_ok_age": last_ok_age,
                "format_float": _format_float,
            },
        )

    @app.get("/operator", response_class=HTMLResponse)
    def operator_page(request: Request):
        cfg = context.config_store.snapshot()
        state = context.state_store.snapshot()
        last_ok_ts = state.last_ok_ts
        last_ok_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(last_ok_ts)) if last_ok_ts else "-"
        last_ok_age = (time.time() - last_ok_ts) if last_ok_ts else None
        return templates.TemplateResponse(
            "operator.html",
            {
                "request": request,
                "cfg": cfg,
                "state": state,
                "body_class": _body_class("operator", state),
                "last_ok_str": last_ok_str,
                "last_ok_age": last_ok_age,
                "format_float": _format_float,
                "title": "Operator Live",
            },
        )

    @app.get("/diagnostics", response_class=HTMLResponse)
    def diagnostics_page(request: Request):
        cfg = context.config_store.snapshot()
        state = context.state_store.snapshot()
        return templates.TemplateResponse(
            "diagnostics.html",
            {
                "request": request,
                "cfg": cfg,
                "state": state,
                "body_class": _body_class("dashboard", state),
                "now": time.time(),
                "format_float": _format_float,
            },
        )

    def _render_targets(request: Request):
        cfg = context.config_store.snapshot()
        state = context.state_store.snapshot()
        offsets_in = [mm_to_in(v) for v in cfg.offsets_mm]
        return templates.TemplateResponse(
            "config.html",
            {
                "request": request,
                "cfg": cfg,
                "state": state,
                "body_class": _body_class("targets", state),
                "offsets_in": offsets_in,
                "format_float": _format_float,
                "title": "Targets & Offsets",
            },
        )

    @app.get("/targets", response_class=HTMLResponse)
    def targets_page(request: Request):
        return _render_targets(request)

    @app.get("/config", response_class=HTMLResponse)
    def config_page(request: Request):
        return _render_targets(request)

    def _apply_form(data: Dict[str, str]) -> None:
        context.config_store.update_from_form(data)

    def _apply_and_redirect(data: Dict[str, str], redirect_url: str, save: bool) -> RedirectResponse:
        _apply_form(data)
        if save:
            context.config_store.save()
        return RedirectResponse(url=redirect_url, status_code=303)

    def _collect_form(
        d1_target_in: float = Form(...),
        d1_tol_in: float = Form(...),
        d2_target_in: float = Form(...),
        d2_tol_in: float = Form(...),
        len_target_in: float = Form(...),
        len_tol_in: float = Form(...),
        ddelta_max_in: float = Form(...),
        end1_max_in: float = Form(...),
        end2_max_in: float = Form(...),
        off0_in: float = Form(...),
        off1_in: float = Form(...),
        off2_in: float = Form(...),
        off3_in: float = Form(...),
        off4_in: float = Form(...),
        off5_in: float = Form(...),
        off6_in: float = Form(...),
        off7_in: float = Form(...),
    ) -> Dict[str, str]:
        return {
            "d1_target_in": str(d1_target_in),
            "d1_tol_in": str(d1_tol_in),
            "d2_target_in": str(d2_target_in),
            "d2_tol_in": str(d2_tol_in),
            "len_target_in": str(len_target_in),
            "len_tol_in": str(len_tol_in),
            "ddelta_max_in": str(ddelta_max_in),
            "end1_max_in": str(end1_max_in),
            "end2_max_in": str(end2_max_in),
            "off0_in": str(off0_in),
            "off1_in": str(off1_in),
            "off2_in": str(off2_in),
            "off3_in": str(off3_in),
            "off4_in": str(off4_in),
            "off5_in": str(off5_in),
            "off6_in": str(off6_in),
            "off7_in": str(off7_in),
        }

    @app.post("/targets/apply")
    def targets_apply(
        d1_target_in: float = Form(...),
        d1_tol_in: float = Form(...),
        d2_target_in: float = Form(...),
        d2_tol_in: float = Form(...),
        len_target_in: float = Form(...),
        len_tol_in: float = Form(...),
        ddelta_max_in: float = Form(...),
        end1_max_in: float = Form(...),
        end2_max_in: float = Form(...),
        off0_in: float = Form(...),
        off1_in: float = Form(...),
        off2_in: float = Form(...),
        off3_in: float = Form(...),
        off4_in: float = Form(...),
        off5_in: float = Form(...),
        off6_in: float = Form(...),
        off7_in: float = Form(...),
    ):
        data = _collect_form(
            d1_target_in,
            d1_tol_in,
            d2_target_in,
            d2_tol_in,
            len_target_in,
            len_tol_in,
            ddelta_max_in,
            end1_max_in,
            end2_max_in,
            off0_in,
            off1_in,
            off2_in,
            off3_in,
            off4_in,
            off5_in,
            off6_in,
            off7_in,
        )
        return _apply_and_redirect(data, "/targets", False)

    @app.post("/targets/save")
    def targets_save(
        d1_target_in: float = Form(...),
        d1_tol_in: float = Form(...),
        d2_target_in: float = Form(...),
        d2_tol_in: float = Form(...),
        len_target_in: float = Form(...),
        len_tol_in: float = Form(...),
        ddelta_max_in: float = Form(...),
        end1_max_in: float = Form(...),
        end2_max_in: float = Form(...),
        off0_in: float = Form(...),
        off1_in: float = Form(...),
        off2_in: float = Form(...),
        off3_in: float = Form(...),
        off4_in: float = Form(...),
        off5_in: float = Form(...),
        off6_in: float = Form(...),
        off7_in: float = Form(...),
    ):
        data = _collect_form(
            d1_target_in,
            d1_tol_in,
            d2_target_in,
            d2_tol_in,
            len_target_in,
            len_tol_in,
            ddelta_max_in,
            end1_max_in,
            end2_max_in,
            off0_in,
            off1_in,
            off2_in,
            off3_in,
            off4_in,
            off5_in,
            off6_in,
            off7_in,
        )
        return _apply_and_redirect(data, "/targets", True)

    @app.post("/config/apply")
    def config_apply(
        d1_target_in: float = Form(...),
        d1_tol_in: float = Form(...),
        d2_target_in: float = Form(...),
        d2_tol_in: float = Form(...),
        len_target_in: float = Form(...),
        len_tol_in: float = Form(...),
        ddelta_max_in: float = Form(...),
        end1_max_in: float = Form(...),
        end2_max_in: float = Form(...),
        off0_in: float = Form(...),
        off1_in: float = Form(...),
        off2_in: float = Form(...),
        off3_in: float = Form(...),
        off4_in: float = Form(...),
        off5_in: float = Form(...),
        off6_in: float = Form(...),
        off7_in: float = Form(...),
    ):
        data = _collect_form(
            d1_target_in,
            d1_tol_in,
            d2_target_in,
            d2_tol_in,
            len_target_in,
            len_tol_in,
            ddelta_max_in,
            end1_max_in,
            end2_max_in,
            off0_in,
            off1_in,
            off2_in,
            off3_in,
            off4_in,
            off5_in,
            off6_in,
            off7_in,
        )
        return _apply_and_redirect(data, "/targets", False)

    @app.post("/config/save")
    def config_save(
        d1_target_in: float = Form(...),
        d1_tol_in: float = Form(...),
        d2_target_in: float = Form(...),
        d2_tol_in: float = Form(...),
        len_target_in: float = Form(...),
        len_tol_in: float = Form(...),
        ddelta_max_in: float = Form(...),
        end1_max_in: float = Form(...),
        end2_max_in: float = Form(...),
        off0_in: float = Form(...),
        off1_in: float = Form(...),
        off2_in: float = Form(...),
        off3_in: float = Form(...),
        off4_in: float = Form(...),
        off5_in: float = Form(...),
        off6_in: float = Form(...),
        off7_in: float = Form(...),
    ):
        data = _collect_form(
            d1_target_in,
            d1_tol_in,
            d2_target_in,
            d2_tol_in,
            len_target_in,
            len_tol_in,
            ddelta_max_in,
            end1_max_in,
            end2_max_in,
            off0_in,
            off1_in,
            off2_in,
            off3_in,
            off4_in,
            off5_in,
            off6_in,
            off7_in,
        )
        return _apply_and_redirect(data, "/targets", True)

    @app.get("/wifi", response_class=HTMLResponse)
    def wifi_page(request: Request):
        cfg = context.config_store.snapshot()
        state = context.state_store.snapshot()
        rescan = request.query_params.get("rescan") == "1"
        networks, active_ssid, ip_addr, scan_error = _list_wifi_networks(rescan)
        message = request.query_params.get("message")
        message_kind = request.query_params.get("status") or ""
        return templates.TemplateResponse(
            "wifi.html",
            {
                "request": request,
                "cfg": cfg,
                "state": state,
                "body_class": _body_class("wifi", state),
                "networks": networks,
                "active_ssid": active_ssid,
                "ip_addr": ip_addr,
                "scan_error": scan_error,
                "message": message,
                "message_kind": message_kind,
                "format_float": _format_float,
                "title": "WiFi",
            },
        )

    @app.post("/wifi/connect")
    def wifi_connect(
        ssid: str = Form(...),
        password: str = Form(""),
    ):
        ok, msg = _connect_wifi(ssid.strip(), password.strip())
        context.log_buffer.add("INFO", f"WiFi connect attempt: {ssid.strip()}", "wifi")
        status = "pass" if ok else "fail"
        return RedirectResponse(
            url=f"/wifi?status={status}&message={quote(msg)}",
            status_code=303,
        )

    @app.get("/logs", response_class=HTMLResponse)
    def logs_page(request: Request):
        cfg = context.config_store.snapshot()
        state = context.state_store.snapshot()
        logs = context.log_buffer.snapshot()
        logs.reverse()
        return templates.TemplateResponse(
            "logs.html",
            {
                "request": request,
                "cfg": cfg,
                "state": state,
                "body_class": _body_class("dashboard", state),
                "logs": logs,
                "format_float": _format_float,
            },
        )

    return app
