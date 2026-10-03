"""
Anti-dumping measure lookup API — IBM Code Engine deployment
Workshop: Bulgarian Customs Agency · AI with IBM watsonx Orchestrate

Exposes one POST endpoint:
  POST /lookup

No authentication — open endpoint for workshop use.
Deploy to IBM Code Engine, copy the public URL into anti_dumping_openapi.json.

Run locally:
  pip install fastapi uvicorn
  uvicorn anti_dumping_api:app --reload --port 8080
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Embedded measure table — identical logic to anti_dumping_lookup.py
#
# ILLUSTRATIVE DEMO DATA — NOT THE LIVE ANTI-DUMPING RULEBOOK
#
# PRODUCTION REPLACEMENT POINT:
#   Replace _MEASURES with a SQL query against watsonx.data / TARIC:
#       SELECT cn_code, origin_iso2, measure_type, rate_percent,
#              rate_per_unit_eur, min_price_eur
#       FROM   taric.anti_dumping_measures
#       WHERE  cn_code = :cn_code AND origin_iso2 = :origin_iso2
#       AND    CURRENT_DATE BETWEEN valid_from AND COALESCE(valid_to, DATE '9999-12-31')
# ---------------------------------------------------------------------------

AD_VALOREM = "ad_valorem"
SPECIFIC   = "specific"
VARIABLE   = "variable"


@dataclass(frozen=True)
class _Measure:
    cn_code: str
    origin_iso2: str
    measure_type: str
    rate_percent: float
    rate_per_unit_eur: float
    min_price_eur: float
    regulation_ref: str
    product_description: str


_MEASURES: list[_Measure] = [
    _Measure("7318159589", "CN", AD_VALOREM, 87.5, 0.0, 0.0,
             "Regulation (EU) 2022/191 (illustrative)",
             "Стоманени болтове и гайки (HS 7318), произход Китай"),
    _Measure("7207200119", "RU", AD_VALOREM, 37.6, 0.0, 0.0,
             "Regulation (EU) 2016/1377 (illustrative)",
             "Плоски валцувани стоманени продукти (HS 7207), произход Русия"),
    _Measure("8519893000", "CN", AD_VALOREM, 22.3, 0.0, 0.0,
             "Regulation (EU) 2020/509 (illustrative)",
             "Аудиооборудване (HS 8519), произход Китай"),
    _Measure("0306171090", "EC", VARIABLE, 0.0, 0.0, 3.53,
             "Regulation (EU) 2013/185 (illustrative)",
             "Скариди (HS 0306), произход Еквадор — ценово задължение"),
]

_INDEX: dict[tuple[str, str], _Measure] = {
    (m.cn_code, m.origin_iso2.upper()): m for m in _MEASURES
}

# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class LookupRequest(BaseModel):
    cn_code: str = Field(..., description="8-digit CN code, digits only, e.g. '7318159589'")
    origin_country: str = Field(..., description="ISO 3166-1 alpha-2 country code, e.g. 'CN'")
    customs_value: float = Field(0.0, description="Customs value in EUR")
    quantity_kg: float = Field(0.0, description="Net mass in kg (required for variable measures)")
    import_price_per_kg: float = Field(0.0, description="Declared import price per kg in EUR (required for variable measures)")
    exporter: Optional[str] = Field(None, description="Exporter name or TARIC additional code (optional)")


class LookupResponse(BaseModel):
    cn_code: str
    origin_country: str
    customs_value_eur: float
    exporter: str
    measure_found: bool
    measure_type: Optional[str] = None
    rate_percent: Optional[float] = None
    min_price_eur_per_kg: Optional[float] = None
    import_price_per_kg: Optional[float] = None
    quantity_kg: Optional[float] = None
    calculated_duty_eur: Optional[float] = None
    calculation_note_bg: Optional[str] = None
    message_bg: str
    regulation_ref: Optional[str] = None
    product_description: Optional[str] = None
    disclaimer_bg: str


# ---------------------------------------------------------------------------
# Core calculation logic
# ---------------------------------------------------------------------------

DISCLAIMER = (
    "⚠️ Данните са илюстративни и са предназначени единствено за демонстрация. "
    "Окончателното решение за прилагане на мярката се взема от митническия служител "
    "въз основа на официалните правни актове на ЕС."
)


def _calculate(req: LookupRequest) -> LookupResponse:
    clean_cn     = req.cn_code.replace(" ", "").replace("-", "").replace(".", "").strip()
    clean_origin = req.origin_country.strip().upper()
    measure      = _INDEX.get((clean_cn, clean_origin))

    base = dict(
        cn_code=clean_cn,
        origin_country=clean_origin,
        customs_value_eur=round(req.customs_value, 2),
        exporter=req.exporter or "не е посочен",
        disclaimer_bg=DISCLAIMER,
    )

    if measure is None:
        return LookupResponse(
            **base,
            measure_found=False,
            message_bg=(
                f"Не е намерена активна антидъмпингова мярка на ЕС за CN код {clean_cn} "
                f"с произход {clean_origin}. "
                "Моля, проверете в официалната база данни ТАРИК / OLAF преди окончателно решение."
            ),
        )

    if measure.measure_type == AD_VALOREM:
        duty = round(req.customs_value * measure.rate_percent / 100, 2)
        return LookupResponse(
            **base,
            measure_found=True,
            measure_type=AD_VALOREM,
            rate_percent=measure.rate_percent,
            calculated_duty_eur=duty,
            regulation_ref=measure.regulation_ref,
            product_description=measure.product_description,
            calculation_note_bg=(
                f"Мито = митническа стойност × ставка / 100  →  "
                f"{req.customs_value:.2f} EUR × {measure.rate_percent}% / 100 = {duty:.2f} EUR"
            ),
            message_bg=(
                f"✅ Намерена е антидъмпингова мярка (ad valorem) за CN {clean_cn} / {clean_origin}. "
                f"Ставка: {measure.rate_percent}%. Дължимо антидъмпингово мито: **{duty:.2f} EUR**."
            ),
        )

    if measure.measure_type == VARIABLE:
        if req.quantity_kg <= 0:
            return LookupResponse(
                **base,
                measure_found=True,
                measure_type=VARIABLE,
                min_price_eur_per_kg=measure.min_price_eur,
                regulation_ref=measure.regulation_ref,
                product_description=measure.product_description,
                message_bg=(
                    f"✅ Намерена е антидъмпингова мярка (variable / ценово задължение) "
                    f"за CN {clean_cn} / {clean_origin}. "
                    f"Минимална цена: {measure.min_price_eur:.2f} EUR/кг. "
                    "Предоставете quantity_kg и import_price_per_kg за изчисление на конкретното мито."
                ),
            )
        if req.import_price_per_kg >= measure.min_price_eur:
            return LookupResponse(
                **base,
                measure_found=True,
                measure_type=VARIABLE,
                min_price_eur_per_kg=measure.min_price_eur,
                import_price_per_kg=req.import_price_per_kg,
                calculated_duty_eur=0.0,
                regulation_ref=measure.regulation_ref,
                product_description=measure.product_description,
                calculation_note_bg=(
                    f"Декларирана цена ({req.import_price_per_kg:.2f} EUR/кг) ≥ "
                    f"минимална цена ({measure.min_price_eur:.2f} EUR/кг) → мито = 0 EUR "
                    "(ценовото задължение е спазено)."
                ),
                message_bg=(
                    f"✅ Намерена е антидъмпингова мярка (variable) за CN {clean_cn} / {clean_origin}, "
                    f"но декларираната цена ({req.import_price_per_kg:.2f} EUR/кг) е равна или "
                    f"по-висока от минималната импортна цена ({measure.min_price_eur:.2f} EUR/кг). "
                    "**Мито: 0 EUR** (ценовото задължение е спазено)."
                ),
            )
        duty = round((measure.min_price_eur - req.import_price_per_kg) * req.quantity_kg, 2)
        return LookupResponse(
            **base,
            measure_found=True,
            measure_type=VARIABLE,
            min_price_eur_per_kg=measure.min_price_eur,
            import_price_per_kg=req.import_price_per_kg,
            quantity_kg=req.quantity_kg,
            calculated_duty_eur=duty,
            regulation_ref=measure.regulation_ref,
            product_description=measure.product_description,
            calculation_note_bg=(
                f"Мито = (минимална цена − декларирана цена) × количество  →  "
                f"({measure.min_price_eur:.2f} − {req.import_price_per_kg:.2f}) × "
                f"{req.quantity_kg:.2f} кг = {duty:.2f} EUR"
            ),
            message_bg=(
                f"✅ Намерена е антидъмпингова мярка (variable) за CN {clean_cn} / {clean_origin}. "
                f"Декларираната цена ({req.import_price_per_kg:.2f} EUR/кг) е под минималната "
                f"({measure.min_price_eur:.2f} EUR/кг). **Дължимо антидъмпингово мито: {duty:.2f} EUR**."
            ),
        )

    # specific — placeholder
    return LookupResponse(
        **base,
        measure_found=True,
        measure_type=SPECIFIC,
        regulation_ref=measure.regulation_ref,
        product_description=measure.product_description,
        message_bg=(
            f"Намерена е мярка (specific) за CN {clean_cn} / {clean_origin}. "
            "Предоставете количеството за изчисление."
        ),
    )


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Anti-Dumping Measure Lookup API",
    description=(
        "Lookup EU anti-dumping measures and calculate duty. "
        "Illustrative demo data — Bulgarian customs workshop."
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    """Health check for Code Engine."""
    return {"status": "ok"}


@app.post("/lookup", response_model=LookupResponse)
def lookup(req: LookupRequest):
    """
    Look up an EU anti-dumping measure and calculate the duty owed.

    Provide cn_code and origin_country at minimum.
    For variable measures also provide quantity_kg and import_price_per_kg.
    """
    return _calculate(req)
