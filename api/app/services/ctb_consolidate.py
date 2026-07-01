"""Pipeline de consolidação e ingestão do CTB no dataset `seguramultas_ctb`.

Opção B — Planalto como base, livros enriquecem
-----------------------------------------------
Para cada artigo do CTB (1..341), monta UM chunk consolidado contendo, nesta ordem:
  1. TEXTO OFICIAL DA LEI (Planalto — completo, atual, domínio público)
  2. COMENTÁRIO doutrinário (Celso, casado por número — quando existe)
  3. (referências do 360, quando agregam)
  4. RESOLUÇÕES CONTRAN citadas (estruturadas no metadata — munição de defesa)
Cada fonte carrega sua ficha bibliográfica para citação (LDA art. 46).

Além dos artigos, ingere a RESOLUÇÃO 432/2013 inteira (chunks por artigo dela) — base
das teses de defesa em embriaguez (art. 165/165-A/306).

Uso (no container da VPS, igual ao MBFT):
    python scripts/ingest_ctb.py --planalto /caminho/ctb_planalto.html \
        --celso /caminho/celso.pdf --leg360 /caminho/360.pdf \
        --res432 /caminho/resolucao_432_2013.txt \
        --dataset seguramultas_ctb --write-db --write-rag
"""
from __future__ import annotations

import re

from app.services.ctb_parser import ArtigoChunk, BIBLIO, parse_livro
from app.services.ctb_planalto_parser import parse_planalto


# --------------------------------------------------------------------------- #
# Resolução 432/2013 embutida (texto oficial, domínio público — LDA art. 8º).
# Embutida no código para não depender de arquivo no container.
# --------------------------------------------------------------------------- #
RESOLUCAO_432_TEXTO = """RESOLUÇÃO CONTRAN Nº 432, DE 23 DE JANEIRO DE 2013

Dispõe sobre os procedimentos a serem adotados pelas autoridades de trânsito e seus agentes na fiscalização do consumo de álcool ou de outra substância psicoativa que determine dependência, para aplicação do disposto nos arts. 165, 276, 277 e 306 da Lei nº 9.503, de 23 de setembro de 1997 – Código de Trânsito Brasileiro (CTB).

O CONSELHO NACIONAL DE TRÂNSITO, no uso das atribuições que lhe confere o art. 12, inciso I, da Lei nº 9.503, de 23 de setembro de 1997, que institui o Código de Trânsito Brasileiro, e nos termos do disposto no Decreto nº 4.711, de 29 de maio de 2003, que trata da coordenação do Sistema Nacional de Trânsito.

CONSIDERANDO a nova redação dos art. 165, 276, 277 e 302, da Lei nº 9.503, de 23 de setembro de 1997, dada pela Lei nº 12.760, de 20 de dezembro de 2012;
CONSIDERANDO o estudo da Associação Brasileira de Medicina de Tráfego, ABRAMET, acerca dos procedimentos médicos para fiscalização do consumo de álcool ou de outra substância psicoativa que determine dependência pelos condutores; e
CONSIDERANDO o disposto nos processos nºs 80001.005410/2006-70, 80001.002634/2006-20 e 80000.000042/2013-11;

RESOLVE,

Art. 1º Definir os procedimentos a serem adotados pelas autoridades de trânsito e seus agentes na fiscalização do consumo de álcool ou de outra substância psicoativa que determine dependência, para aplicação do disposto nos arts. 165, 276, 277 e 306 da Lei nº 9.503, de 23 de setembro de 1997 – Código de Trânsito Brasileiro (CTB).

Art. 2º A fiscalização do consumo, pelos condutores de veículos automotores, de bebidas alcoólicas e de outras substâncias psicoativas que determinem dependência deve ser procedimento operacional rotineiro dos órgãos de trânsito.

Art. 3º A confirmação da alteração da capacidade psicomotora em razão da influência de álcool ou de outra substância psicoativa que determine dependência dar-se-á por meio de, pelo menos, um dos seguintes procedimentos a serem realizados no condutor de veículo automotor:
I – exame de sangue;
II – exames realizados por laboratórios especializados, indicados pelo órgão ou entidade de trânsito competente ou pela Polícia Judiciária, em caso de consumo de outras substâncias psicoativas que determinem dependência;
III – teste em aparelho destinado à medição do teor alcoólico no ar alveolar (etilômetro);
IV – verificação dos sinais que indiquem a alteração da capacidade psicomotora do condutor.
§ 1º Além do disposto nos incisos deste artigo, também poderão ser utilizados prova testemunhal, imagem, vídeo ou qualquer outro meio de prova em direito admitido.
§ 2º Nos procedimentos de fiscalização deve-se priorizar a utilização do teste com etilômetro.
§ 3º Se o condutor apresentar sinais de alteração da capacidade psicomotora na forma do art. 5º ou haja comprovação dessa situação por meio do teste de etilômetro e houver encaminhamento do condutor para a realização do exame de sangue ou exame clínico, não será necessário aguardar o resultado desses exames para fins de autuação administrativa.

DO TESTE DE ETILÔMETRO

Art. 4º O etilômetro deve atender aos seguintes requisitos:
I – ter seu modelo aprovado pelo INMETRO;
II – ser aprovado na verificação metrológica inicial, eventual, em serviço e anual realizadas pelo Instituto Nacional de Metrologia, Qualidade e Tecnologia - INMETRO ou por órgão da Rede Brasileira de Metrologia Legal e Qualidade - RBMLQ;
Parágrafo único. Do resultado do etilômetro (medição realizada) deverá ser descontada margem de tolerância, que será o erro máximo admissível, conforme legislação metrológica, de acordo com a "Tabela de Valores Referenciais para Etilômetro" constante no Anexo I.

DOS SINAIS DE ALTERAÇÃO DA CAPACIDADE PSICOMOTORA

Art. 5º Os sinais de alteração da capacidade psicomotora poderão ser verificados por:
I – exame clínico com laudo conclusivo e firmado por médico perito; ou
II – constatação, pelo agente da Autoridade de Trânsito, dos sinais de alteração da capacidade psicomotora nos termos do Anexo II.
§ 1º Para confirmação da alteração da capacidade psicomotora pelo agente da Autoridade de Trânsito, deverá ser considerado não somente um sinal, mas um conjunto de sinais que comprovem a situação do condutor.
§ 2º Os sinais de alteração da capacidade psicomotora de que trata o inciso II deverão ser descritos no auto de infração ou em termo específico que contenha as informações mínimas indicadas no Anexo II, o qual deverá acompanhar o auto de infração.

DA INFRAÇÃO ADMINISTRATIVA

Art. 6º A infração prevista no art. 165 do CTB será caracterizada por:
I – exame de sangue que apresente qualquer concentração de álcool por litro de sangue;
II – teste de etilômetro com medição realizada igual ou superior a 0,05 miligrama de álcool por litro de ar alveolar expirado (0,05 mg/L), descontado o erro máximo admissível nos termos da "Tabela de Valores Referenciais para Etilômetro" constante no Anexo I;
III – sinais de alteração da capacidade psicomotora obtidos na forma do art. 5º.
Parágrafo único. Serão aplicadas as penalidades e medidas administrativas previstas no art. 165 do CTB ao condutor que recusar a se submeter a qualquer um dos procedimentos previstos no art. 3º, sem prejuízo da incidência do crime previsto no art. 306 do CTB caso o condutor apresente os sinais de alteração da capacidade psicomotora.

DO CRIME

Art. 7º O crime previsto no art. 306 do CTB será caracterizado por qualquer um dos procedimentos abaixo:
I – exame de sangue que apresente resultado igual ou superior a 6 (seis) decigramas de álcool por litro de sangue (6 dg/L);
II – teste de etilômetro com medição realizada igual ou superior a 0,34 miligrama de álcool por litro de ar alveolar expirado (0,34 mg/L), descontado o erro máximo admissível nos termos da "Tabela de Valores Referenciais para Etilômetro" constante no Anexo I;
III – exames realizados por laboratórios especializados, indicados pelo órgão ou entidade de trânsito competente ou pela Polícia Judiciária, em caso de consumo de outras substâncias psicoativas que determinem dependência;
IV – sinais de alteração da capacidade psicomotora obtido na forma do art. 5º.
§ 1º A ocorrência do crime de que trata o caput não elide a aplicação do disposto no art. 165 do CTB.
§ 2º Configurado o crime de que trata este artigo, o condutor e testemunhas, se houver, serão encaminhados à Polícia Judiciária, devendo ser acompanhados dos elementos probatórios.

DO AUTO DE INFRAÇÃO

Art. 8º Além das exigências estabelecidas em regulamentação específica, o auto de infração lavrado em decorrência da infração prevista no art. 165 do CTB deverá conter:
I – no caso de encaminhamento do condutor para exame de sangue, exame clínico ou exame em laboratório especializado, a referência a esse procedimento;
II – no caso do art. 5º, os sinais de alteração da capacidade psicomotora de que trata o Anexo II ou a referência ao preenchimento do termo específico de que trata o § 2º do art. 5º;
III – no caso de teste de etilômetro, a marca, modelo e nº de série do aparelho, nº do teste, a medição realizada, o valor considerado e o limite regulamentado em mg/L;
IV – conforme o caso, a identificação da(s) testemunha(s), se houve fotos, vídeos ou outro meio de prova complementar, se houve recusa do condutor, entre outras informações disponíveis.
§ 1º Os documentos gerados e o resultado dos exames de que trata o inciso I deverão ser anexados ao auto de infração.
§ 2º No caso do teste de etilômetro, para preenchimento do campo "Valor Considerado" do auto de infração, deve-se observar as margens de erro admissíveis, nos termos da "Tabela de Valores Referenciais para Etilômetro" constante no Anexo I.

DAS MEDIDAS ADMINISTRATIVAS

Art. 9º O veículo será retido até a apresentação de condutor habilitado, que também será submetido à fiscalização.
Parágrafo único. Caso não se apresente condutor habilitado ou o agente verifique que ele não está em condições de dirigir, o veículo será recolhido ao depósito do órgão ou entidade responsável pela fiscalização, mediante recibo.

Art. 10. O documento de habilitação será recolhido pelo agente, mediante recibo, e ficará sob custódia do órgão ou entidade de trânsito responsável pela autuação até que o condutor comprove que não está com a capacidade psicomotora alterada, nos termos desta Resolução.
§ 1º Caso o condutor não compareça ao órgão ou entidade de trânsito responsável pela autuação no prazo de 5 (cinco) dias da data do cometimento da infração, o documento será encaminhado ao órgão executivo de trânsito responsável pelo seu registro, onde o condutor deverá buscar seu documento.
§ 2º A informação de que trata o § 1º deverá constar no recibo de recolhimento do documento de habilitação.

DISPOSIÇÕES GERAIS

Art. 11. É obrigatória a realização do exame de alcoolemia para as vítimas fatais de acidentes de trânsito.

Art. 12. Ficam convalidados os atos praticados na vigência da Deliberação CONTRAN nº 133, de 21 de dezembro de 2012, com o reconhecimento da margem de tolerância de que trata o art. 1º da Deliberação CONTRAN referida no caput (0,10 mg/L) como limite regulamentar.

Art. 13. Ficam revogadas as Resoluções CONTRAN nº 109, de 21 de Novembro de 1999, e nº 206, de 20 de outubro de 2006, e a Deliberação CONTRAN nº 133, de 21 de dezembro de 2012.

Art. 14. Esta Resolução entra em vigor na data de sua publicação.

MORVAM COTRIM DUARTE - Presidente em Exercício

Este texto não substitui o publicado no DOU de 29.01.2013.
"""


def parse_resolucao_432_embutida() -> list[dict]:
    """Parseia a Resolução 432/2013 a partir do texto embutido no código."""
    return parse_resolucao_432(RESOLUCAO_432_TEXTO, is_text=True)


def _num_int(num: str) -> tuple[int, str]:
    b = re.match(r"(\d+)", num)
    return (int(b.group(1)) if b else 9999, num)


def _index_por_artigo(chunks: list[ArtigoChunk]) -> dict[str, ArtigoChunk]:
    """Indexa chunks por número de artigo (último vence — já vêm consolidados)."""
    return {c.art_numero: c for c in chunks}


def consolidar_ctb(
    *,
    planalto_html: str | None = None,
    celso_pdf: str | None = None,
    leg360_pdf: str | None = None,
) -> list[dict]:
    """Casa as 3 fontes por número de artigo e devolve chunks consolidados (dicts prontos
    para Postgres/RAGFlow). Planalto é a base; Celso e 360 enriquecem.

    Cada chunk consolidado tem:
      content: texto montado (LEI + COMENTÁRIO)
      metadata: art_numero, ctb_article, fontes_usadas, resolucoes_citadas,
                citacao_lei, citacao_doutrina, alteracoes_legais, source_type
    """
    base = _index_por_artigo(parse_planalto(planalto_html)) if planalto_html else {}
    celso = _index_por_artigo(parse_livro(celso_pdf, "ctb_comentado_celso")) if celso_pdf else {}
    leg360 = _index_por_artigo(parse_livro(leg360_pdf, "ctb_360")) if leg360_pdf else {}

    # universo de artigos = união das três fontes (Planalto cobre quase tudo)
    todos = sorted(set(base) | set(celso) | set(leg360), key=_num_int)

    consolidados: list[dict] = []
    for num in todos:
        b = base.get(num)
        c = celso.get(num)
        g = leg360.get(num)

        partes: list[str] = []
        fontes_usadas: list[str] = []
        resolucoes: list[dict] = []
        citacao_lei = None
        citacao_doutrina = None
        alteracoes: list[str] = []

        # 1) LEI — prioriza Planalto (oficial); cai no 360 e depois no Celso
        fonte_lei = b or g or c
        if fonte_lei is not None:
            partes.append(f"LEI — {fonte_lei.metadata['ctb_article']} (CTB):\n{fonte_lei.texto}")
            fontes_usadas.append(fonte_lei.fonte)
            citacao_lei = fonte_lei.metadata.get("citacao")
            alteracoes = fonte_lei.metadata.get("alteracoes_legais", []) or []

        # 2) COMENTÁRIO — do Celso, quando tem doutrina de fato
        if c is not None and c.tem_comentario:
            partes.append(
                f"COMENTÁRIO DOUTRINÁRIO ({c.metadata['autor']}):\n{c.texto}"
            )
            if "ctb_comentado_celso" not in fontes_usadas:
                fontes_usadas.append("ctb_comentado_celso")
            citacao_doutrina = c.metadata.get("citacao")

        # 3) RESOLUÇÕES citadas — agrega de todas as fontes (dedup por número)
        vistos = set()
        for src in (c, g, b):
            if src is None:
                continue
            for r in src.metadata.get("resolucoes_citadas", []) or []:
                if r["numero"] not in vistos:
                    vistos.add(r["numero"])
                    resolucoes.append(r)

        content = "\n\n".join(partes).strip()
        if not content:
            continue

        # source_type: doutrina se há comentário; senão lei
        source_type = "doutrina" if (c is not None and c.tem_comentario) else "lei"

        consolidados.append({
            "article": f"Art. {num}",
            "content": content,
            "metadata": {
                "art_numero": num,
                "ctb_article": f"Art. {num}",
                "dataset": "ctb",
                "source": "ctb_consolidado",
                "source_type": source_type,
                "fontes_usadas": fontes_usadas,
                "resolucoes_citadas": resolucoes,
                "alteracoes_legais": alteracoes,
                "tem_comentario": bool(c is not None and c.tem_comentario),
                "citacao_lei": citacao_lei,
                "citacao_doutrina": citacao_doutrina,
                "allow_verbatim": True,
            },
        })
    return consolidados


def parse_resolucao_432(txt_path_or_text: str, *, is_text: bool = False) -> list[dict]:
    """Parseia a Resolução 432/2013 em chunks por artigo dela.

    `txt_path_or_text` pode ser um CAMINHO de arquivo (padrão) ou o próprio TEXTO
    (quando is_text=True) — este último caso é usado com o texto embutido no código,
    para não depender de arquivo no container.
    """
    biblio = BIBLIO["resolucao_432"]
    texto = txt_path_or_text if is_text else open(txt_path_or_text, encoding="utf-8").read()

    art_re = re.compile(r"(?m)^\s*Art\.\s*(\d+)[º°]?\.?")
    matches = list(art_re.finditer(texto))
    # cabeçalho (antes do Art. 1º): ementa + considerandos → vira um chunk "ementa"
    out: list[dict] = []
    if matches:
        cab = texto[:matches[0].start()].strip()
        if len(cab) > 50:
            out.append({
                "article": "Res. 432/2013 — Ementa",
                "content": cab,
                "metadata": {
                    "art_numero": "0", "dataset": "ctb", "source": "resolucao_432",
                    "source_type": "lei", "resolucao": "432/2013",
                    "citacao": f'{biblio["obra"]}. {biblio["editora"]}, {biblio["ano"]}.',
                    "allow_verbatim": True,
                },
            })
    for i, m in enumerate(matches):
        ini = m.start()
        fim = matches[i + 1].start() if i + 1 < len(matches) else len(texto)
        bloco = texto[ini:fim].strip()
        if len(bloco) < 15:
            continue
        num = m.group(1)
        out.append({
            "article": f"Res. 432/2013, Art. {num}",
            "content": bloco,
            "metadata": {
                "art_numero": num,
                "dataset": "ctb",
                "source": "resolucao_432",
                "source_type": "lei",
                "resolucao": "432/2013",
                "ctb_artigos_relacionados": ["165", "165-A", "276", "277", "306"],
                "citacao": (
                    f'{biblio["obra"]}, art. {num}. {biblio["editora"]}, {biblio["ano"]}.'
                ),
                "allow_verbatim": True,
            },
        })
    return out


def relatorio(consolidados: list[dict], res432: list[dict]) -> dict:
    com_doutrina = sum(1 for c in consolidados if c["metadata"]["tem_comentario"])
    com_resolucao = sum(1 for c in consolidados if c["metadata"]["resolucoes_citadas"])
    return {
        "artigos_ctb": len(consolidados),
        "com_comentario_doutrina": com_doutrina,
        "com_resolucoes_citadas": com_resolucao,
        "chunks_resolucao_432": len(res432),
        "total_chunks": len(consolidados) + len(res432),
    }
