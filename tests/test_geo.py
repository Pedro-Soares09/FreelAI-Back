import pytest

from app.services.geo import arredondar_km, caixa_para_raio, distancia_km

VARZEA = (-8.0505, -34.9500)
BOA_VIAGEM = (-8.1195, -34.9005)


def test_distancia_entre_varzea_e_boa_viagem_e_de_cerca_de_nove_km():
    distancia = distancia_km(*VARZEA, *BOA_VIAGEM)

    assert 9.0 <= distancia <= 10.0
    assert arredondar_km(distancia) == pytest.approx(9.4, abs=0.1)


def test_distancia_e_simetrica_e_zero_no_mesmo_ponto():
    assert distancia_km(*VARZEA, *VARZEA) == 0.0
    assert distancia_km(*VARZEA, *BOA_VIAGEM) == pytest.approx(distancia_km(*BOA_VIAGEM, *VARZEA))


def test_um_grau_de_latitude_tem_cerca_de_cento_e_onze_km():
    assert distancia_km(0.0, 0.0, 1.0, 0.0) == pytest.approx(111.2, abs=0.2)


def test_arredondar_km_usa_uma_casa_decimal():
    assert arredondar_km(9.4137) == 9.4
    assert arredondar_km(0.96) == 1.0


def test_caixa_envolve_o_raio_e_exclui_o_que_esta_longe():
    caixa = caixa_para_raio(*VARZEA, 10)

    assert caixa.contem(*BOA_VIAGEM)
    assert caixa.contem(*VARZEA)
    assert not caixa.contem(VARZEA[0] + 0.2, VARZEA[1])
    assert not caixa.contem(VARZEA[0], VARZEA[1] + 0.2)


def test_caixa_e_apenas_um_pre_filtro_mais_largo_que_o_circulo():
    caixa = caixa_para_raio(*VARZEA, 10)
    canto = (caixa.lat_max, caixa.lon_max)

    assert caixa.contem(*canto)
    assert distancia_km(*VARZEA, *canto) > 10


def test_caixa_respeita_os_limites_do_globo():
    caixa = caixa_para_raio(89.99, 179.99, 50)

    assert caixa.lat_max == 90.0
    assert (caixa.lon_min, caixa.lon_max) == (-180.0, 180.0)


def test_caixa_que_cruza_o_antimeridiano_abre_a_volta_inteira():
    caixa = caixa_para_raio(0.0, 179.95, 20)

    assert caixa.contem(0.0, -179.95)
