"""Linha de comando: `mcp-planalto` (sem argumentos) inicia o servidor MCP via stdio."""

from __future__ import annotations

import argparse
import json
import logging
import sys


def _imprimir(obj) -> None:
    if isinstance(obj, str):
        print(obj)
    else:
        print(json.dumps(obj, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        prog="mcp-planalto",
        description="Legislação federal brasileira (Planalto + Senado) via MCP e linha de comando.",
    )
    p.add_argument("-v", "--verboso", action="store_true", help="mostra o log detalhado")
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("servir", help="inicia o servidor MCP (padrão)")
    s.add_argument("--http", action="store_true", help="usa streamable-http em vez de stdio")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--porta", type=int, default=8000)

    s = sub.add_parser("sincronizar", help="monta/atualiza o catálogo local")
    s.add_argument("--tipos", nargs="+", help="LEI LCP DEC DEL MPV EMC LDL (padrão: todos)")
    s.add_argument("--desde-ano", type=int, help="só normas a partir deste ano")
    s.add_argument("--senado", action="store_true", help="também baixa as listas anuais do Senado (apelidos)")
    s.add_argument("--detalhes", action="store_true",
                   help="baixa indexação e alterações do Senado para cada norma (demorado, retomável)")
    s.add_argument("--so-detalhes", action="store_true", help="pula os quadros do Planalto")
    s.add_argument("--limite", type=int, help="máximo de normas a detalhar")
    s.add_argument("--paralelo", type=int, default=4, help="requisições simultâneas ao Senado")

    s = sub.add_parser("ler", help="lê o texto atualizado de uma norma")
    s.add_argument("referencia")
    s.add_argument("-d", "--dispositivo")
    s.add_argument("-t", "--termo")
    s.add_argument("--historico", action="store_true", help="inclui redações anteriores")
    s.add_argument("--sem-notas", action="store_true")
    s.add_argument("--estrutura", action="store_true", help="mostra só o sumário")
    s.add_argument("--pagina", type=int, default=1)

    s = sub.add_parser("ficha", help="metadados da norma")
    s.add_argument("referencia")

    s = sub.add_parser("buscar", help="busca no catálogo")
    s.add_argument("consulta")
    s.add_argument("--tipos", nargs="+")
    s.add_argument("--desde-ano", type=int)
    s.add_argument("--ate-ano", type=int)
    s.add_argument("--limite", type=int, default=20)

    s = sub.add_parser("historico", help="quem alterou a norma (ou um dispositivo)")
    s.add_argument("referencia")
    s.add_argument("-d", "--dispositivo")
    s.add_argument("--desde")
    s.add_argument("--feitas", action="store_true", help="normas que esta alterou")

    s = sub.add_parser("verificar", help="confere se o texto do Planalto está atualizado")
    s.add_argument("referencia")

    s = sub.add_parser("novidades", help="normas publicadas recentemente")
    s.add_argument("--dias", type=int, default=30)
    s.add_argument("--desde")
    s.add_argument("--tema")
    s.add_argument("--tipos", nargs="+")

    s = sub.add_parser("mapear", help="mapeia o acervo de um tema e exporta planilha")
    s.add_argument("tema")
    s.add_argument("--termos", nargs="+", help="termos extras")
    s.add_argument("--normas", nargs="+", help="normas extras para o núcleo")
    s.add_argument("--tipos", nargs="+")
    s.add_argument("--desde-ano", type=int)
    s.add_argument("--ate-ano", type=int)
    s.add_argument("--profundidade", type=int, default=1)
    s.add_argument("--formato", choices=["xlsx", "csv"], default="xlsx")

    sub.add_parser("temas", help="lista os temas pré-configurados")
    sub.add_parser("status", help="estado do catálogo local")

    a = p.parse_args(argv)
    if a.cmd in (None, "servir"):
        from .server import main as servir

        if a.cmd == "servir" and a.http:
            servir("http", a.host, a.porta)
        else:
            servir()
        return

    logging.basicConfig(level=logging.INFO if a.verboso else logging.WARNING,
                        format="%(levelname)s %(message)s", stream=sys.stderr)
    from .servico import Legislacao, NormaNaoEncontrada

    L = Legislacao()
    progresso = lambda m: print(m, file=sys.stderr)  # noqa: E731
    try:
        if a.cmd == "sincronizar":
            if not a.so_detalhes:
                _imprimir(L.sincronizar_catalogo(a.tipos, a.desde_ano, senado=a.senado, progresso=progresso))
            if a.detalhes or a.so_detalhes:
                _imprimir(L.sincronizar_detalhes(a.tipos, a.desde_ano, a.limite, a.paralelo, progresso=progresso))
        elif a.cmd == "ler":
            r = L.texto(a.referencia, a.dispositivo, a.termo, "historico" if a.historico else "vigente",
                        notas=not a.sem_notas, estrutura=a.estrutura, pagina=a.pagina)
            if "texto" in r:
                texto = r.pop("texto")
                _imprimir({k: v for k, v in r.items() if k != "notas_gerais"})
                print()
                print(texto)
            else:
                _imprimir(r)
        elif a.cmd == "ficha":
            _imprimir(L.ficha(a.referencia))
        elif a.cmd == "buscar":
            _imprimir(L.buscar(a.consulta, a.tipos, a.desde_ano, a.ate_ano, a.limite))
        elif a.cmd == "historico":
            _imprimir(L.historico(a.referencia, a.dispositivo, a.desde, "feitas" if a.feitas else "recebidas"))
        elif a.cmd == "verificar":
            _imprimir(L.verificar_atualizacao(a.referencia))
        elif a.cmd == "novidades":
            _imprimir(L.novidades(a.desde, a.dias, a.tipos, a.tema))
        elif a.cmd == "mapear":
            from .exportar import exportar

            r = L.mapear_tema(a.tema, a.termos, a.normas, a.tipos, a.desde_ano, a.ate_ano,
                              profundidade=a.profundidade, progresso=progresso)
            caminho = exportar(r["normas"], L.config.export_dir, f"acervo-{r['tema']}", a.formato,
                               titulo=f"Acervo de legislação federal: {r['tema']}")
            resumo = {k: v for k, v in r.items() if k != "normas"}
            resumo["primeiras"] = [f"{n['norma']} [{n['camada']}] {n['relevancia']}" for n in r["normas"][:25]]
            resumo["arquivo"] = str(caminho)
            _imprimir(resumo)
        elif a.cmd == "temas":
            from .temas import TEMAS

            for t in TEMAS.values():
                print(f"{t.sigla:26} {t.nome}")
        elif a.cmd == "status":
            _imprimir(L.status())
    except (NormaNaoEncontrada, ValueError) as e:
        print(f"Erro: {e}", file=sys.stderr)
        for c in getattr(e, "candidatos", []):
            print(f"  - {c.nome} ({c.data}): {(c.ementa or '')[:90]}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
