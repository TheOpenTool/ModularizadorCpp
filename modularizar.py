#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Modulariza un archivo C++ grande en unidades de a lo sumo N lineas.

Uso:
    modularizar.py <archivo.cpp> [opciones]

Opciones:
    --spec <spec.json>     cortes y reparto a mano (ver README). Sin --spec se
                           eligen los cortes solos (modo automatico).
    --max <N>              tamano maximo de cada archivo resultante (450).
    --cmake <archivo>      CMakeLists donde registrar las partes nuevas (se
                           puede repetir). Se agregan junto a cada aparicion
                           del archivo original.
    --tests                modo para suites de test: las funciones 'static' y
                           las libres se reparten por posicion, se declaran en
                           el header interno y quitan sus valores por defecto.
    --abrir-anonimos       (con --tests) reparte tambien lo que esta dentro de
                           un namespace anonimo.
    --aplicar              escribe los archivos (sin esto solo muestra el plan).

Despues de aplicar, verifica contra git HEAD que el codigo sea el mismo linea a
linea (ver modularizador/verificar.py) e informa cualquier diferencia que no
sea de las que la division introduce a proposito.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

AQUI = os.path.dirname(os.path.abspath(__file__))
DIVIDIR = os.path.join(AQUI, "modularizador", "dividir.py")
VERIFICAR = os.path.join(AQUI, "modularizador", "verificar.py")

# Diferencias que la division introduce a proposito (no son errores).
ESPERADAS = [
    r"SOBRA x\d+: .*\(.*\);$",                       # prototipos generados
    r"(SOBRA|FALTA) x\d+: (namespace \{|\}|// Funciones repartidas)",
    r"FALTA x\d+: static ",                          # 'static' quitado al repartir
]


def correr_division(archivo, spec, aplicar):
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(spec, f)
        ruta_spec = f.name
    try:
        args = [sys.executable, DIVIDIR, archivo, ruta_spec] + (["--aplicar"] if aplicar else [])
        r = subprocess.run(args, capture_output=True, text=True)
    finally:
        os.unlink(ruta_spec)
    if r.returncode != 0:
        return None, r.stdout + r.stderr
    salidas = [(int(n), f) for n, f in re.findall(r"^\s*(\d+) lineas\s+(\S+)", r.stdout, re.M)]
    return salidas, r.stdout


def registrar_en_cmake(cmake, original, partes):
    """Agrega las partes junto a cada aparicion del original en el CMakeLists.

    Se reconoce una aparicion por el nombre del archivo precedido de '/' (con
    cualquier prefijo: variables como ${SRC_DIR}/ o rutas relativas) cuyo
    directorio coincide con el del original o usa una variable de CMake.
    """
    s = open(cmake, encoding="utf-8").read()
    nombre = os.path.basename(original)
    carpeta = os.path.basename(os.path.dirname(os.path.abspath(original)))
    patron = re.compile(r"(^|[\s(\"])(\S*/)?" + re.escape(nombre) + r"(?=[\s)\"])", re.M)
    total = 0

    def agregar(m):
        nonlocal total
        prefijo = m.group(2) or ""
        directorio = prefijo.rstrip("/").split("/")[-1] if prefijo else ""
        if prefijo and directorio != carpeta and "${" not in prefijo and directorio not in ("", "."):
            return m.group(0)
        total += 1
        extra = "".join(" " + prefijo + p for p in partes if (prefijo + p) not in s)
        return m.group(0) + extra

    nuevo = patron.sub(agregar, s)
    if nuevo != s:
        open(cmake, "w", encoding="utf-8").write(nuevo)
    return total


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("archivo")
    ap.add_argument("--spec")
    ap.add_argument("--max", type=int, default=450)
    ap.add_argument("--cmake", action="append", default=[])
    ap.add_argument("--tests", action="store_true")
    ap.add_argument("--abrir-anonimos", action="store_true")
    ap.add_argument("--aplicar", action="store_true")
    a = ap.parse_args()

    if a.spec:
        spec = json.load(open(a.spec, encoding="utf-8"))
        candidatos = [spec]
    else:
        candidatos = []
        for auto_max in range(a.max - 50, 100, -20):
            spec = {"auto_max": auto_max}
            if a.tests:
                spec.update({"externalizar_static": True, "prototipos_original": True,
                             "abrir_anonimos": a.abrir_anonimos})
            candidatos.append(spec)

    elegido = None
    for spec in candidatos:
        salidas, texto = correr_division(a.archivo, spec, aplicar=False)
        if salidas is None:
            print(texto)
            return 1
        if all(n <= a.max for n, _ in salidas):
            elegido = spec
            break
    if elegido is None:
        print("No se encontro un reparto con todas las partes <= %d lineas." % a.max)
        print("Suele pasar con una sola funcion mas grande que el limite: hay que")
        print("partirla a mano (por ejemplo, en funciones auxiliares) y volver a correr.")
        return 1

    print("Plan:", " ".join("%s(%d)" % (os.path.basename(f), n) for n, f in salidas))
    if not a.aplicar:
        return 0

    salidas, texto = correr_division(a.archivo, elegido, aplicar=True)
    archivos = [f for _, f in salidas]
    v = subprocess.run([sys.executable, VERIFICAR, a.archivo] + archivos, capture_output=True, text=True)
    inesperadas = [l for l in v.stdout.splitlines()
                   if not l.startswith("OK") and not any(re.match(e, l) for e in ESPERADAS)]
    if inesperadas:
        print("VERIFICACION: diferencias a revisar")
        for l in inesperadas:
            print("  " + l)
    else:
        print("Verificacion: mismo codigo.")

    nombre = os.path.basename(a.archivo)
    partes = [os.path.basename(f) for f in archivos
              if f.endswith(".cpp") and os.path.basename(f) != nombre]
    for cm in a.cmake:
        print("%s: %d listados actualizados" % (cm, registrar_en_cmake(cm, a.archivo, partes)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
