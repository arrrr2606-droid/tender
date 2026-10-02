from monitor import regions


def test_resolve_user_input():
    assert regions.resolve("Татарстан") == ["16"]
    assert regions.resolve("МО") == ["50"]
    assert regions.resolve("Московская область") == ["50"]
    assert regions.resolve("Москва") == ["77"]
    assert regions.resolve("спб") == ["78"]
    assert regions.resolve("ХМАО") == ["86"]
    assert regions.resolve("16") == ["16"]
    assert regions.resolve("свердловская") == ["66"]
    assert "66" in regions.resolve("УФО")
    assert regions.resolve("Нарния") == []


def test_detect_in_text():
    assert regions.detect_in_text("АДМИНИСТРАЦИЯ ЗАВЬЯЛОВСКИЙ РАЙОН УДМУРТСКОЙ РЕСПУБЛИКИ") == "18"
    assert regions.detect_in_text("г. Москва, ул. Ленина") == "77"
    assert regions.detect_in_text("Московская обл., г. Химки") == "50"
    assert regions.detect_in_text("Нижегородская область") == "52"
    assert regions.detect_in_text("Великий Новгород") == "53"
    assert regions.detect_in_text("Томская область") == "70"   # не путать с Омской
    assert regions.detect_in_text("г. Омск") == "55"
    assert regions.detect_in_text("ООО Ромашка") == ""


def test_inn():
    assert regions.from_inn("1655000000") == "16"
    assert regions.from_inn("9901000000") == ""   # 99 — межрегиональная инспекция
    assert regions.from_inn("abc") == ""
