import json
import calendar
from datetime import date, datetime
from pathlib import Path
from io import BytesIO
import uuid

import streamlit as st
import pandas as pd
from streamlit_sortables import sort_items  # pip install streamlit-sortables

from reportlab.lib.pagesizes import A4, landscape
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet



MESES_PT = {
    1: "Janeiro", 2: "Fevereiro", 3: "Março", 4: "Abril", 5: "Maio", 6: "Junho",
    7: "Julho", 8: "Agosto", 9: "Setembro", 10: "Outubro", 11: "Novembro", 12: "Dezembro"
}

STATUSES = [
    "Atendimento 08:00-14:00",
    "Atendimento 12:00-18:00",
    "Laboratório",
    "Blip 08:00-14:00",
    "Blip 12:00-18:00",
    "Banco de horas",
    "Férias",
]
DEFAULT_STATUS = "Atendimento 08:00-14:00"
HEADERS = {stt: stt for stt in STATUSES}

META_CLOSED_KEY = "__closed__"  # sábado feriado/fechado (ninguém trabalha)

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

EMPLOYEES_JSON = DATA_DIR / "employees.json"
SCHEDULE_JSON = DATA_DIR / "schedule_sabados.json"
CONSIDERATIONS_JSON = DATA_DIR / "consideracoes.json"


CONFIG_EXPORT_VERSION = 3



def load_json(path: Path, default):
    try:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return default


def save_json(path: Path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass



def get_saturdays(year: int, month: int):
    last_day = calendar.monthrange(year, month)[1]
    out = []
    for d in range(1, last_day + 1):
        dt = date(year, month, d)
        if dt.weekday() == 5:  # sábado
            out.append(dt)
    return out


def iso(d: date) -> str:
    return d.isoformat()



def ensure_month_schedule(schedule: dict, year: int, month: int, people: list[str]) -> dict:
    month_key = f"{year:04d}-{month:02d}"
    if month_key not in schedule:
        schedule[month_key] = {}

    sats = get_saturdays(year, month)
    sats_iso = [iso(d) for d in sats]

    # remove sábados que não existem mais
    for k in list(schedule[month_key].keys()):
        if k not in sats_iso:
            schedule[month_key].pop(k, None)

    # garante todos sábados existentes com todos os status + meta
    for s in sats_iso:
        if s not in schedule[month_key]:
            schedule[month_key][s] = {stt: [] for stt in STATUSES}
            schedule[month_key][s][META_CLOSED_KEY] = False
            schedule[month_key][s][DEFAULT_STATUS] = list(people)
        else:
            if META_CLOSED_KEY not in schedule[month_key][s]:
                schedule[month_key][s][META_CLOSED_KEY] = False

            for stt in STATUSES:
                schedule[month_key][s].setdefault(stt, [])

            if schedule[month_key][s].get(META_CLOSED_KEY) is True:
                for stt in STATUSES:
                    schedule[month_key][s][stt] = [p for p in schedule[month_key][s][stt] if p in people]
                continue

            assigned = []
            for stt in STATUSES:
                schedule[month_key][s][stt] = [p for p in schedule[month_key][s][stt] if p in people]
                assigned.extend(schedule[month_key][s][stt])

            missing = [p for p in people if p not in set(assigned)]
            if missing:
                schedule[month_key][s][DEFAULT_STATUS].extend(missing)

    return schedule


def get_month_schedule(schedule: dict, year: int, month: int) -> dict:
    month_key = f"{year:04d}-{month:02d}"
    return schedule.get(month_key, {})


def sanitize_day(day_map: dict, people: list[str]) -> dict:
    day_map.setdefault(META_CLOSED_KEY, False)

    for stt in STATUSES:
        day_map.setdefault(stt, [])

    if day_map.get(META_CLOSED_KEY) is True:
        for stt in STATUSES:
            day_map[stt] = []
        return day_map

    seen = set()
    for stt in STATUSES:
        new_list = []
        for n in day_map[stt]:
            if n in people and n not in seen:
                new_list.append(n)
                seen.add(n)
        day_map[stt] = new_list

    missing = [p for p in people if p not in seen]
    if missing:
        day_map[DEFAULT_STATUS].extend(missing)

    return day_map


def day_map_to_sortables(day_map: dict) -> list[dict]:
    return [{"header": HEADERS[stt], "items": list(day_map.get(stt, []))} for stt in STATUSES]


def sortables_to_day_map(sorted_items: list[dict]) -> dict:
    header_to_status = {HEADERS[stt]: stt for stt in STATUSES}
    out = {stt: [] for stt in STATUSES}

    for col in sorted_items:
        header = col.get("header")
        items = col.get("items", [])
        stt = header_to_status.get(header)
        if stt:
            out[stt] = [x for x in items if isinstance(x, str) and x.strip()]

    return out



def build_month_summary(month_schedule: dict, saturdays: list[date], people: list[str]) -> pd.DataFrame:
    counts = {p: {stt: 0 for stt in STATUSES} for p in people}

    for sat in saturdays:
        sat_iso = iso(sat)
        day_map = month_schedule.get(sat_iso)
        if not isinstance(day_map, dict):
            continue

        if day_map.get(META_CLOSED_KEY) is True:
            continue

        for stt in STATUSES:
            for name in day_map.get(stt, []):
                if name in counts:
                    counts[name][stt] += 1

    df = pd.DataFrame.from_dict(counts, orient="index")
    df.index.name = "Colaborador"
    df = df.reset_index()

    df["Total"] = df[STATUSES].sum(axis=1)
    df = df.sort_values(["Total", "Colaborador"], ascending=[False, True]).drop(columns=["Total"])
    return df


def ensure_considerations_struct(data: dict) -> dict:
    if not isinstance(data, dict):
        data = {}
    if "months" not in data or not isinstance(data["months"], dict):
        data["months"] = {}
    return data


def get_month_considerations(cons_data: dict, month_key: str) -> list[dict]:
    cons_data = ensure_considerations_struct(cons_data)
    lst = cons_data["months"].get(month_key, [])
    if not isinstance(lst, list):
        lst = []
        cons_data["months"][month_key] = lst
    return lst



def _p(text: str, styles, style_name="Normal"):
    return Paragraph(text, styles[style_name])


def _add_considerations_to_story(story, styles, considerations: list[dict]):
    story.append(Spacer(1, 10))
    story.append(_p("<b>Considerações</b>", styles, "Heading2"))
    story.append(Spacer(1, 6))

    if not considerations:
        story.append(_p("— Nenhuma consideração cadastrada.", styles))
        return

    for it in considerations:
        txt = (it.get("text") or "").strip()
        if txt:
            story.append(_p(f"• {txt}", styles))


def make_summary_pdf(summary_df: pd.DataFrame, year: int, month: int, n_people: int, considerations: list[dict]) -> bytes:
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=18, rightMargin=18, topMargin=18, bottomMargin=18)
    styles = getSampleStyleSheet()
    story = []

    title = f"Resumo do mês - {MESES_PT.get(month, str(month))}/{year}"
    story.append(_p(title, styles, "Title"))
    story.append(Spacer(1, 6))
    story.append(_p(f"<b>Funcionários cadastrados:</b> {n_people}", styles))
    story.append(Spacer(1, 10))

    cols = ["Colaborador"] + STATUSES
    data = [cols]
    for _, r in summary_df.iterrows():
        data.append([str(r["Colaborador"])] + [str(int(r[c])) for c in STATUSES])

    page_w, _ = landscape(A4)
    usable_w = page_w - doc.leftMargin - doc.rightMargin
    col_widths = [150] + [(usable_w - 150) / len(STATUSES)] * len(STATUSES)

    table = Table(data, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
        ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (1, 1), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(table)

    _add_considerations_to_story(story, styles, considerations)

    doc.build(story)
    return buf.getvalue()


def make_schedule_pdf(month_schedule: dict, saturdays: list[date], year: int, month: int, n_people: int, considerations: list[dict]) -> bytes:
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=18, rightMargin=18, topMargin=18, bottomMargin=18)
    styles = getSampleStyleSheet()
    story = []

    title = f"Escala de Sábados - {MESES_PT.get(month, str(month))}/{year}"
    story.append(_p(title, styles, "Title"))
    story.append(Spacer(1, 6))
    story.append(_p(f"<b>Funcionários cadastrados:</b> {n_people}", styles))
    story.append(Spacer(1, 10))

    page_w, _ = landscape(A4)
    usable_w = page_w - doc.leftMargin - doc.rightMargin
    col_widths = [usable_w / len(STATUSES)] * len(STATUSES)

    for i, sat in enumerate(saturdays):
        sat_iso = iso(sat)
        day_map = month_schedule.get(sat_iso, {stt: [] for stt in STATUSES})
        is_closed = bool(day_map.get(META_CLOSED_KEY))

        story.append(_p(f"<b>{sat.strftime('%d/%m/%Y')}</b>", styles, "Heading2"))
        story.append(Spacer(1, 6))

        if is_closed:
            story.append(_p("<b>FERIADO / SEM ESCALA (ninguém trabalha)</b>", styles))
            story.append(Spacer(1, 10))
        else:
            header_row = [HEADERS[stt] for stt in STATUSES]
            values_row = []
            for stt in STATUSES:
                names = day_map.get(stt, [])
                if names:
                    html = "<br/>".join([f"• {n}" for n in names])
                else:
                    html = "-"
                values_row.append(_p(html, styles))

            table = Table([header_row, values_row], colWidths=col_widths)
            table.setStyle(TableStyle([
                ("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
                ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 9),
                ("FONTSIZE", (0, 1), (-1, 1), 8),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (0, 0), (-1, 0), "CENTER"),
            ]))
            story.append(table)

        if i % 2 == 1 and i != len(saturdays) - 1:
            story.append(PageBreak())
        else:
            story.append(Spacer(1, 14))

    _add_considerations_to_story(story, styles, considerations)

    doc.build(story)
    return buf.getvalue()


def build_export_package(people: list[str], schedule_months: dict, considerations_months: dict) -> dict:
    return {
        "type": "intelbras_sabados_config",
        "version": CONFIG_EXPORT_VERSION,
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "employees": list(people),
        "months": schedule_months,
        "considerations": considerations_months,  
    }


def validate_import_package(pkg: dict) -> tuple[bool, str]:
    if not isinstance(pkg, dict):
        return False, "Arquivo inválido (não é um JSON objeto)."
    if pkg.get("type") != "intelbras_sabados_config":
        return False, "Tipo de arquivo não reconhecido."
    if not isinstance(pkg.get("version"), int):
        return False, "Versão ausente/ inválida."
    employees = pkg.get("employees")
    months = pkg.get("months")
    if not isinstance(employees, list) or not all(isinstance(x, str) and x.strip() for x in employees):
        return False, "Campo 'employees' inválido."
    if not isinstance(months, dict):
        return False, "Campo 'months' inválido."

    # considerações são opcionais 
    cons = pkg.get("considerations", {})
    if cons is not None and not isinstance(cons, dict):
        return False, "Campo 'considerations' inválido."

    return True, "OK"


def apply_import_package(pkg: dict):
    people = [x.strip() for x in pkg.get("employees", []) if isinstance(x, str) and x.strip()]
    seen = set()
    people_clean = []
    for p in people:
        if p not in seen:
            people_clean.append(p)
            seen.add(p)

    months = pkg.get("months", {})
    if not isinstance(months, dict):
        months = {}

    cons_months = pkg.get("considerations", {})
    if not isinstance(cons_months, dict):
        cons_months = {}

    save_json(EMPLOYEES_JSON, {"employees": people_clean})
    save_json(SCHEDULE_JSON, {"months": months})
    save_json(CONSIDERATIONS_JSON, {"months": cons_months})

    st.session_state.people = people_clean
    st.session_state.schedule = months
    st.session_state.considerations = {"months": cons_months}



st.set_page_config(page_title="Escala de Sábados - Intelbras", layout="wide")


st.markdown(
    """
<style>
:root{
  --intel-green: #00B140;
  --intel-green-2: #00D04A;
  --intel-black: #070A0E;
  --intel-dark: #0B1118;
  --intel-text: #FFFFFF;
  --intel-muted: rgba(255,255,255,0.78);

  --intel-blue: #1E40AF;
  --intel-blue-2: #1D4ED8;
}

/* Background geral (sem gradiente) */
[data-testid="stAppViewContainer"]{ background: var(--intel-black) !important; }
[data-testid="stSidebar"]{
  background: var(--intel-dark) !important;
  border-right: 1px solid rgba(255,255,255,0.06);
}
html, body, [class*="css"]{ color: var(--intel-text) !important; }

/* Header */
.intel-header{ display:flex; align-items:flex-end; gap:14px; margin: 6px 0 8px 0; }
.intel-logo{ font-weight: 1000; font-size: 44px; letter-spacing: -1px; color: var(--intel-green); text-shadow: none; }
.intel-sub{ color: var(--intel-muted); font-size: 14px; padding-bottom: 8px; }
.small-note{ color: var(--intel-muted); font-size: 0.95rem; }

/* Scroll horizontal sábados */
#sat-scroll + div[data-testid="stHorizontalBlock"]{
  overflow-x: auto !important;
  flex-wrap: nowrap !important;
  gap: 18px !important;
  padding-bottom: 12px;
}
#sat-scroll + div[data-testid="stHorizontalBlock"] > div[data-testid="column"]{
  min-width: 980px !important;
  max-width: 980px !important;
}

/* Card do sábado */
.sat-card{
  border-radius: 16px;
  padding: 12px;
  border: 1px solid rgba(255,255,255,0.10);
  background: linear-gradient(180deg, rgba(0,177,64,0.07), rgba(255,255,255,0.02));
  box-shadow: 0 10px 24px rgba(0,0,0,0.38);
  position: relative;
}

/* Título do sábado */
.block-title{
  background: linear-gradient(90deg, rgba(0,177,64,0.90), rgba(0,208,74,0.85));
  color: #061007 !important;
  font-weight: 1000;
  text-align: center;
  padding: 10px;
  border-radius: 12px;
  border: 1px solid rgba(0,0,0,0.35);
  margin-bottom: 10px;
}

/* Sortables */
.sortable-container{
  background: rgba(255,255,255,0.035);
  border: 1px solid rgba(255,255,255,0.10);
  border-radius: 12px;
  padding: 10px;
  min-height: 220px;
}
.sortable-container h3{
  margin: 0 0 10px 0;
  padding: 8px;
  background: rgba(0,177,64,0.12);
  color: rgba(255,255,255,0.92);
  border-radius: 10px;
  text-align: center;
  font-weight: 1000;
  border: 1px solid rgba(0,177,64,0.28);
}

/* Itens */
.sortable-item{
  background: linear-gradient(90deg, var(--intel-blue), var(--intel-blue-2)) !important;
  color: #FFFFFF !important;
  font-weight: 1000 !important;
  border-radius: 10px !important;
  padding: 10px 12px !important;
  margin-bottom: 8px !important;
  border: 1px solid rgba(255,255,255,0.14) !important;
  box-shadow: 0 4px 10px rgba(0,0,0,0.22) !important;
}

/* Alertas */
div[data-testid="stAlert"]{
  border-radius: 12px;
  border: 1px solid rgba(255,255,255,0.10);
  background: rgba(255,255,255,0.04);
}

/* Botões */
.stButton>button, .stDownloadButton>button{
  background: linear-gradient(90deg, rgba(0,177,64,0.95), rgba(0,208,74,0.90)) !important;
  color: #061007 !important;
  font-weight: 900 !important;
  border: 0 !important;
  border-radius: 10px !important;
  box-shadow: 0 6px 14px rgba(0,0,0,0.22) !important;
}
.stButton>button:hover, .stDownloadButton>button:hover{ filter: brightness(1.02); }

/* Inputs */
.stTextInput input, .stNumberInput input, .stSelectbox select, .stTextArea textarea{
  background: rgba(255,255,255,0.06) !important;
  color: #FFFFFF !important;
  border: 1px solid rgba(255,255,255,0.12) !important;
  border-radius: 10px !important;
}
</style>
""",
    unsafe_allow_html=True
)

st.markdown(
    """
<div class="intel-header">
  <div class="intel-logo">Escala Sábado</div>
  <div class="intel-sub">Escala de Sábados • Gestão de status por sábado</div>
</div>
""",
    unsafe_allow_html=True
)

# Estado
if "people" not in st.session_state:
    emp = load_json(EMPLOYEES_JSON, {"employees": []})
    st.session_state.people = emp.get("employees", []) if isinstance(emp, dict) else []
    if not isinstance(st.session_state.people, list):
        st.session_state.people = []

if "schedule" not in st.session_state:
    st.session_state.schedule = load_json(SCHEDULE_JSON, {"months": {}}).get("months", {})

if "considerations" not in st.session_state:
    st.session_state.considerations = ensure_considerations_struct(
        load_json(CONSIDERATIONS_JSON, {"months": {}})
    )

if "active_year" not in st.session_state:
    st.session_state.active_year = date.today().year
if "active_month" not in st.session_state:
    st.session_state.active_month = date.today().month


# Sidebar
with st.sidebar:
    st.header("Configurações")

    c1, c2 = st.columns(2)
    with c1:
        year = st.number_input("Ano", 2020, 2100, int(st.session_state.active_year), 1)
    with c2:
        month = st.number_input("Mês", 1, 12, int(st.session_state.active_month), 1)

    st.divider()
    st.subheader("Colaboradores")

    new_person = st.text_input("Adicionar colaborador", placeholder="Nome do colaborador")
    if st.button("Adicionar", use_container_width=True):
        p = new_person.strip()
        if p and p not in st.session_state.people:
            st.session_state.people.append(p)
            save_json(EMPLOYEES_JSON, {"employees": st.session_state.people})
            st.success(f"Adicionado: {p}")
        elif not p:
            st.warning("Nome vazio.")
        else:
            st.warning("Esse colaborador já existe.")

    if st.session_state.people:
        rem = st.selectbox("Remover colaborador", ["(nenhum)"] + st.session_state.people)
        if st.button("Remover", use_container_width=True):
            if rem != "(nenhum)":
                st.session_state.people = [x for x in st.session_state.people if x != rem]
                save_json(EMPLOYEES_JSON, {"employees": st.session_state.people})
                st.success(f"Removido: {rem}")

    st.caption("Dica: ao mudar o mês, todos começam em Atendimento 08:00-14:00 em cada sábado.")

    st.divider()
    st.subheader("Exportar / Importar")

    export_pkg = build_export_package(
        st.session_state.people,
        st.session_state.schedule,
        ensure_considerations_struct(st.session_state.considerations).get("months", {})
    )
    export_bytes = json.dumps(export_pkg, ensure_ascii=False, indent=2).encode("utf-8")

    st.download_button(
        "Baixar Configurações do sistema",
        data=export_bytes,
        file_name=f"config_intelbras_sabados_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
        mime="application/json",
        use_container_width=True
    )

    uploaded = st.file_uploader("Importar Configuração (JSON)", type=["json"])
    if uploaded is not None:
        try:
            pkg = json.loads(uploaded.read().decode("utf-8"))
            ok, msg = validate_import_package(pkg)
            if not ok:
                st.error(msg)
            else:
                if st.button("Aplicar Importação", use_container_width=True):
                    apply_import_package(pkg)
                    st.success("Configuração importada com sucesso! Recarregando...")
                    st.rerun()
        except Exception as e:
            st.error(f"Falha ao ler JSON: {e}")


# Preparar mês
year_i = int(year)
month_i = int(month)
st.session_state.active_year = year_i
st.session_state.active_month = month_i

month_key = f"{year_i:04d}-{month_i:02d}"


st.session_state.schedule = ensure_month_schedule(
    st.session_state.schedule,
    year_i,
    month_i,
    st.session_state.people
)
save_json(SCHEDULE_JSON, {"months": st.session_state.schedule})

month_schedule = get_month_schedule(st.session_state.schedule, year_i, month_i)
saturdays = get_saturdays(year_i, month_i)

n_people = len(st.session_state.people)
st.info(f"Funcionários cadastrados: **{n_people}**")
st.markdown(f"**Mês selecionado:** {MESES_PT.get(month_i, str(month_i))}/{year_i}")

if not saturdays:
    st.info("Nenhum sábado encontrado nesse mês.")
    st.stop()

if not st.session_state.people:
    st.warning("Adicione colaboradores na barra lateral para gerar as caixas.")
    st.stop()

# consideraçoes do mês
st.session_state.considerations = ensure_considerations_struct(st.session_state.considerations)
_ = get_month_considerations(st.session_state.considerations, month_key)
save_json(CONSIDERATIONS_JSON, st.session_state.considerations)

current_considerations = get_month_considerations(st.session_state.considerations, month_key)



st.markdown("## Escala do mês")
st.markdown(
    '<div class="small-note">Para trocar o status, arraste o nome de uma coluna para outra dentro do mesmo sábado.</div>',
    unsafe_allow_html=True
)

st.markdown('<div id="sat-scroll"></div>', unsafe_allow_html=True)

cols = st.columns(len(saturdays))
for col, sat_date in zip(cols, saturdays):
    with col:
        sat_iso = iso(sat_date)

        day_map = month_schedule.get(sat_iso, {stt: [] for stt in STATUSES})
        day_map = sanitize_day(day_map, st.session_state.people)

        st.markdown('<div class="sat-card">', unsafe_allow_html=True)
        st.markdown(f'<div class="block-title">{sat_date.strftime("%d/%m/%Y")}</div>', unsafe_allow_html=True)

        closed_key = f"{sat_iso}-closed"
        is_closed_now = bool(day_map.get(META_CLOSED_KEY))
        new_closed = st.checkbox("Sábado feriado (ninguém trabalha)", value=is_closed_now, key=closed_key)

        if new_closed != is_closed_now:
            day_map[META_CLOSED_KEY] = new_closed
            day_map = sanitize_day(day_map, st.session_state.people)
            month_schedule[sat_iso] = day_map

        if day_map.get(META_CLOSED_KEY) is True:
            st.info("Este sábado está marcado como **FERIADO/FECHADO**. Ninguém será escalado e não conta no resumo.")
        else:
            board_items = day_map_to_sortables(day_map)
            sorted_board = sort_items(
                board_items,
                multi_containers=True,
                direction="horizontal",
                custom_style="",
                key=f"{sat_iso}-board"
            )
            if isinstance(sorted_board, list) and sorted_board:
                new_map = sortables_to_day_map(sorted_board)
                new_map[META_CLOSED_KEY] = False
                new_map = sanitize_day(new_map, st.session_state.people)
                month_schedule[sat_iso] = new_map

        st.markdown("</div>", unsafe_allow_html=True)

# salva
st.session_state.schedule[month_key] = month_schedule
save_json(SCHEDULE_JSON, {"months": st.session_state.schedule})
st.success("Alterações salvas automaticamente em data/schedule_sabados.json")

# botão PDF da escala (logo abaixo)
pdf_escala = make_schedule_pdf(month_schedule, saturdays, year_i, month_i, n_people, current_considerations)
st.download_button(
    "Exportar PDF da Escala",
    data=pdf_escala,
    file_name=f"escala_sabados_{year_i:04d}_{month_i:02d}.pdf",
    mime="application/pdf"
)

st.markdown("---")


st.markdown("## Resumo do mês (quantidade por status)")
summary_df = build_month_summary(month_schedule, saturdays, st.session_state.people)
st.dataframe(summary_df, use_container_width=True, hide_index=True)

pdf_resumo = make_summary_pdf(summary_df, year_i, month_i, n_people, current_considerations)
csv_bytes = summary_df.to_csv(index=False).encode("utf-8-sig")

b1, b2 = st.columns(2)
with b1:
    st.download_button(
        "Exportar PDF do Resumo",
        data=pdf_resumo,
        file_name=f"resumo_status_{year_i:04d}_{month_i:02d}.pdf",
        mime="application/pdf",
        use_container_width=True
    )
with b2:
    st.download_button(
        "Exportar CSV do Resumo",
        data=csv_bytes,
        file_name=f"resumo_status_{year_i:04d}_{month_i:02d}.csv",
        mime="text/csv",
        use_container_width=True
    )

st.markdown("---")

st.markdown("## Considerações")


with st.form("cons_form", clear_on_submit=True):
    cons_text = st.text_input("Ex: João precisa trabalhar o primeiro sábado de tarde", placeholder="Digite uma consideração...")
    submitted = st.form_submit_button("Adicionar Consideração")

if submitted:
    txt = (cons_text or "").strip()
    if not txt:
        st.warning("Digite uma consideração antes de adicionar.")
    else:
        item = {"id": str(uuid.uuid4()), "text": txt, "created_at": datetime.now().isoformat(timespec="seconds")}
        st.session_state.considerations["months"][month_key].append(item)
        save_json(CONSIDERATIONS_JSON, st.session_state.considerations)
        st.rerun()

st.markdown("**Considerações feitas:**")

items = st.session_state.considerations["months"].get(month_key, [])
if not items:
    st.caption("Nenhuma consideração cadastrada neste mês.")
else:
    for it in items:
        row1, row2 = st.columns([6, 1])
        with row1:
            st.markdown(f"• {it.get('text','')}")
        with row2:
            if st.button("Remover", key=f"rm-{it['id']}"):
                st.session_state.considerations["months"][month_key] = [
                    x for x in st.session_state.considerations["months"][month_key]
                    if x.get("id") != it["id"]
                ]
                save_json(CONSIDERATIONS_JSON, st.session_state.considerations)
                st.rerun()

# streamlit run .\intelbras-t9.py
