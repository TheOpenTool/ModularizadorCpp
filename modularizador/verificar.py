#!/usr/bin/env python3
"""Verifica que una division no perdio ni agrego codigo.

Uso: verificar.py <ruta del original> <archivo1> <archivo2> ...

El original se lee de git HEAD (ruta relativa a la carpeta actual o absoluta).

Compara el multiconjunto de lineas de codigo (sin blancos, sin la licencia, sin
includes ni lineas generadas por la division) del original (leido de git HEAD)
contra el de los archivos resultantes. Las unicas diferencias admitidas son
las que introduce la herramienta: 'static ' quitado o 'extern' agregado para
las variables de una sola instancia, y las lineas del namespace Detalle.
"""
import collections
import os
import re
import subprocess
import sys

GENERADAS = re.compile(r"^(namespace \{$|// Auxiliares repartidas|namespace [\w:]+ \{$|\}\s*//\s*namespace\b|#ifndef|#define|#endif|// Preambulo compartido|// Estado de la unidad original|"
                       r"namespace \w+Detalle \{|\}  // namespace \w+Detalle|using namespace \w+Detalle;|extern )")


def lineas(texto):
    texto = texto.lstrip("\ufeff")
    if "*/" in texto[:2000]:
        texto = texto[texto.index("*/") + 2:]
    salida = []
    for l in texto.splitlines():
        l = l.strip()
        if not l or l.startswith("#include") or GENERADAS.match(l):
            continue
        if l.startswith("static ") and "(" not in l.split("=")[0]:
            l = l[len("static "):]
        salida.append(l)
    return collections.Counter(salida)


def main():
    ruta = sys.argv[1]
    if os.path.isabs(ruta):
        raiz = subprocess.check_output(["git", "rev-parse", "--show-toplevel"], cwd=os.path.dirname(ruta)).decode().strip()
        objeto = "HEAD:" + os.path.relpath(ruta, raiz)
        original = subprocess.check_output(["git", "show", objeto], cwd=raiz).decode("utf-8")
    else:
        original = subprocess.check_output(["git", "show", "HEAD:./" + ruta]).decode("utf-8")
    a = lineas(original)
    b = collections.Counter()
    for r in sys.argv[2:]:
        b += lineas(open(r, encoding="utf-8").read())
    faltan = a - b
    sobran = b - a
    for l, n in faltan.items():
        print("FALTA x%d: %s" % (n, l))
    for l, n in sobran.items():
        print("SOBRA x%d: %s" % (n, l))
    if not faltan and not sobran:
        print("OK: mismo codigo (%d lineas)" % sum(a.values()))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
