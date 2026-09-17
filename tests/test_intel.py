from jobbot.intel import Candidate, compare, extract_signals, norm_city, norm_company, norm_title, parse_salary, score_offer


def cand(**kw):
    base = dict(title="Aide-soignant H/F", company="Domino RH", city="pamiers", description="", contract="", shift="", source="Hellowork")
    base.update(kw)
    return Candidate(**base)


def test_norm_title_ignores_gender_and_noise():
    assert norm_title("Aide-Soignant H/F") == norm_title("AIDE SOIGNANT (F/H)") == "aidesoignant"
    assert "nuit" in norm_title("Aide-soignant de nuit (H/F)")
    assert "diplome" not in norm_title("Aide-soignant de nuit")


def test_norm_company_and_city():
    assert norm_company("Domino RH") == norm_company("DOMINO RH SAS")
    assert norm_company("Intérim") == ""
    assert norm_city("31 - ST ORENS DE GAMEVILLE") == norm_city("Saint-Orens-de-Gameville - 31")
    assert norm_city("Pamiers (09)") == norm_city("Pamiers - 09") == norm_city("09 - PAMIERS") == "pamiers"


def test_signals_ff_diploma_beginner():
    s = extract_signals("Aide-soignante ou AS FF H/F", "Nous recherchons un aide-soignant ou ASH faisant fonction")
    assert s.ff and not s.diploma_required
    s = extract_signals("Aide-soignant DE (H/F)", "Poste en EHPAD")
    assert s.diploma_required and not s.ff
    assert extract_signals("Aide-Soignant de CDI en EHPAD H/F", "").diploma_required
    assert extract_signals("Aide-Soignant de de Nuit en EHPAD", "").diploma_required
    s = extract_signals("Aide-soignant de nuit", "Rejoignez notre équipe de nuit")
    assert not s.diploma_required and s.shift == "nuit"
    s = extract_signals("Aide-soignant", "Le DEAS est obligatoire pour ce poste.")
    assert s.diploma_required
    s = extract_signals("Aide-soignant", "Diplôme exigé ou faisant fonction accepté")
    assert s.ff and not s.diploma_required
    s = extract_signals("Aide-soignant", "Débutant accepté, formation assurée")
    assert s.beginner_ok


def test_parse_salary():
    assert parse_salary("20 000 - 25 000 € / an") == (1667, 2083)
    assert parse_salary("12,50 € / heure") == (1896, 1896)
    assert parse_salary("1 900 - 2 100 € par mois") == (1900, 2100)
    assert parse_salary("Salaire non précisé") == (None, None)


def test_score_orders_ff_above_diploma():
    ff = extract_signals("Aide-soignant faisant fonction", "")
    de = extract_signals("Aide-soignant DE", "")
    s_ff, reasons = score_offer(ff, "Aide-soignant faisant fonction", 2, 3.0)
    s_de, _ = score_offer(de, "Aide-soignant DE", 2, 3.0)
    assert s_ff > s_de
    assert any("faisant fonction" in r for r in reasons)


def test_compare_cross_source_same_offer():
    a = cand(title="Aide-Soignant H/F", company="Domino RH", source="Hellowork")
    b = cand(title="Aide-soignant (H/F)", company="Domino Rh", source="Meteojob")
    m = compare(a, b)
    assert m.same and m.confidence >= 85


def test_compare_blockers():
    assert not compare(cand(title="Aide-soignant de nuit", source="A"), cand(title="Aide-soignant de jour", source="B")).same
    assert not compare(cand(contract="CDI", source="A"), cand(contract="CDD", source="B")).same
    assert not compare(cand(city="pamiers", source="A"), cand(city="foix", source="B")).same
    assert not compare(cand(company="Korian", source="A"), cand(company="Domino RH", source="B")).same


def test_compare_same_source_distinct_posts_not_merged():
    a = cand(source="France Travail", description="x" * 200 + "EHPAD A")
    b = cand(source="France Travail", description="y" * 200 + "EHPAD B")
    assert not compare(a, b).same


def test_compare_description_match_when_company_unknown():
    desc = "Votre agence recherche des aides-soignants pour intervenir en EHPAD dans la ville de Pamiers. " * 3
    a = cand(company="Intérim", description=desc, source="Jobijoba")
    b = cand(company="Domino RH", description=desc, source="Meteojob")
    assert compare(a, b).same


def test_same_employer_different_posts_not_merged():
    hosp = dict(company="CENTRE HOSPITALIER DE MURET", city="muret")
    assert not compare(cand(title="Aide soignant SSIAD 50%", source="Indeed", **hosp),
                       cand(title="Aide Soignant Nuit Ug 50% H/F", source="Hellowork", **hosp)).same
    assert not compare(cand(title="Aide soignant SSIAD 50%", source="Indeed", **hosp),
                       cand(title="Aide soignant pôle handicap (H/F)", source="France Travail", **hosp)).same
    emeis = dict(company="EMEIS", city="toulouse")
    assert not compare(cand(title="Aide-soignant.e de nuit (H/F)", source="France Travail", **emeis),
                       cand(title="Aide-soignant.e secteur UGD -(31)", source="Indeed", **emeis)).same


def test_city_and_contract_words_do_not_block_merge():
    desc = "Vitalis Médical recrute un aide-soignant diplômé pour un EHPAD situé à Toulouse, poste en CDI temps plein. " * 3
    a = cand(title="Aide-Soignant de CDI EHPAD Toulouse H/F", company="Vitalis Médical", city="toulouse", source="Hellowork", description=desc)
    b = cand(title="Aide-soignant DE H/F CDI EHPAD Toulouse", company="", city="toulouse", source="France Travail", description=desc)
    assert compare(a, b).same
    assert compare(cand(title="Aide-Soignant Asde - Fam - Mas H/F", source="Hellowork"),
                   cand(title="Aide-soignant(e) (ASDE) - FAM/MAS", source="Meteojob")).same


def test_same_site_template_descriptions_not_merged():
    template = "EDENIS, acteur engagé du grand âge, recrute pour sa résidence. Missions : soins d'hygiène et de confort. " * 3
    assert not compare(cand(title="AIDE SOIGNANT(E) NUIT", company="EDENIS", source="Indeed", description=template),
                       cand(title="AIDE SOIGNANT(E)", company="EDENIS", source="Indeed", description=template)).same
    assert not compare(cand(title="Aide-Soignant 70% H/F", company="Korian", source="Hellowork", description=template),
                       cand(title="Aide-Soignant H/F", company="Korian", source="Hellowork", description=template)).same
