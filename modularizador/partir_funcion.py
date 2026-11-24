#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Parte una funcion larga hecha de secciones { ... } en funciones mas chicas.

Uso:
    partir_funcion.py <archivo.cpp> <nombre de la funcion> [--prefijo seccion] [--aplicar]

Cada sentencia de primer nivel del cuerpo que sea un bloque { ... } (con los
comentarios que la preceden) pasa a una funcion 'static void <prefijo>NN(...)'
definida justo antes de la funcion original, y en su lugar queda la llamada. El bloque se copia con sus llaves (lo que
declara sigue en un ambito anidado).
Las variables de la funcion original que el bloque usa se le pasan por
referencia con el mismo nombre ('const T&' si se declararon const), asi el
codigo del bloque no cambia. El resto de las sentencias queda en su lugar.

No se extrae un bloque si usa una variable cuyo tipo no se puede escribir
(declarada con 'auto' o como lambda), si contiene 'return', 'break',
'continue' o 'goto' fuera de una lambda, o si usa una variable que no se pudo
interpretar. Despues de partir, modularizar.py puede repartir las funciones
nuevas en varios archivos.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dividir import tokens_nivel  # noqa: E402

RE_DECL = re.compile(
    r"^(?P<tipo>(?:const\s+)?[A-Za-z_][\w:]*(?:\s*<[^;=(){}]*>)?(?:\s*(?:\*|&)\s*|\s+)(?:const\s*\*\s*)?)"
    r"(?P<nombre>[A-Za-z_]\w*)\s*(?P<resto>=|\(|\{|;|\[)")
CLAVES = {"return", "if", "for", "while", "switch", "do", "else", "case", "delete", "new",
          "throw", "using", "typedef", "goto", "break", "continue", "co_return"}


def sin_comentarios(t):
    t = re.sub(r"//[^\n]*", "", t)
    return re.sub(r"/\*.*?\*/", "", t, flags=re.S)


def sin_cadenas(t):
    t = re.sub(r'R"([^(\s]*)\((.*?)\)\1"', '""', t, flags=re.S)
    return re.sub(r'"(\\.|[^"\\])*"', '""', t)


def sin_lambdas(t):
    """Quita los cuerpos de lambdas: un 'return' adentro no sale de la funcion."""
    salida, i = [], 0
    while i < len(t):
        m = re.compile(r"\]\s*(\([^)]*\))?\s*(mutable\s*)?(->\s*[\w:<>*& ]+)?\s*\{").search(t, i)
        if not m:
            salida.append(t[i:])
            break
        salida.append(t[i:m.end() - 1])
        prof, j = 0, m.end() - 1
        while j < len(t):
            if t[j] == "{":
                prof += 1
            elif t[j] == "}":
                prof -= 1
                if prof == 0:
                    break
            j += 1
        salida.append("{}")
        i = j + 1
    return "".join(salida)


def declaracion(sentencia):
    s = sin_comentarios(sentencia).strip()
    if not s or s.startswith("{") or s.startswith("#"):
        return None
    primera = re.match(r"[A-Za-z_]\w*", s)
    if not primera or primera.group(0) in CLAVES or re.match(r"[A-Za-z_][\w:]*\s*(\(|->|\.|\[|=[^=]|\+\+|--)", s):
        return None
    m = RE_DECL.match(s)
    if not m:
        return None
    tipo = re.sub(r"\s+", " ", m.group("tipo")).strip()
    if re.match(r"(const )?auto\b", tipo) or tipo in ("return", "else"):
        return {"nombre": m.group("nombre"), "tipo": None}
    if m.group("resto") == "[":
        return {"nombre": m.group("nombre"), "tipo": None}  # arreglo: no se pasa
    es_const = tipo.startswith("const ") and not tipo.rstrip().endswith("*")
    base = tipo[len("const "):] if tipo.startswith("const ") and es_const else tipo
    base = base.rstrip("& ").strip()
    if tipo.rstrip().endswith("&"):  # referencia: se pasa del mismo tipo referido
        es_const = tipo.startswith("const ")
    return {"nombre": m.group("nombre"), "tipo": ("const " + base + "&") if es_const else (base + "&")}


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    ruta, funcion = sys.argv[1], sys.argv[2]
    prefijo = "seccion"
    if "--prefijo" in sys.argv:
        prefijo = sys.argv[sys.argv.index("--prefijo") + 1]
    aplicar = "--aplicar" in sys.argv
    texto = open(ruta, encoding="utf-8").read().lstrip("﻿")

    m = re.search(r"^[^\n;{}]*\b" + re.escape(funcion) + r"\s*\([^)]*\)\s*\{", texto, re.M)
    if not m:
        print("ERROR: no encontre la definicion de", funcion)
        return 1
    ini_cuerpo = m.end()
    prof, j = 1, ini_cuerpo
    while prof and j < len(texto):
        if texto[j] == "{":
            prof += 1
        elif texto[j] == "}":
            prof -= 1
        j += 1
    fin_cuerpo = j - 1
    cuerpo = texto[ini_cuerpo:fin_cuerpo]

    declaradas = []  # en orden: {"nombre","tipo"}
    nuevas, cuerpo_nuevo, pendiente = [], [], ""
    cursor = 0
    contador = 0
    for a, b, tipo in tokens_nivel(cuerpo):
        trozo = cuerpo[a:b]
        hueco = cuerpo[cursor:a]
        if tipo == "coment":
            pendiente += hueco + trozo
            cursor = b
            continue
        es_bloque = tipo == "bloque" and trozo.lstrip().startswith("{")
        extraer = False
        if es_bloque:
            limpio = sin_lambdas(sin_cadenas(sin_comentarios(trozo)))
            if not re.search(r"\b(return|break|continue|goto)\b", limpio):
                usadas = [d for d in declaradas if re.search(r"\b" + re.escape(d["nombre"]) + r"\b", limpio)]
                if all(d["tipo"] for d in usadas):
                    extraer = True
        if extraer:
            contador += 1
            nombre = "%s%02d" % (prefijo, contador)
            vistos, params = set(), []
            for d in reversed(usadas):  # la ultima declaracion de un nombre manda
                if d["nombre"] not in vistos:
                    vistos.add(d["nombre"])
                    params.append(d)
            params.reverse()
            firma = ", ".join("%s %s" % (d["tipo"], d["nombre"]) for d in params)
            # El bloque se copia con sus llaves: lo que declara adentro sigue en
            # un ambito anidado (puede ocultar a un parametro, como ocultaba a
            # la variable de la funcion original).
            sangria_bloque = hueco.split("\n")[-1] if "\n" in hueco else "    "
            bloque = sangria_bloque + trozo.strip("\n").lstrip(" ")
            comentario = pendiente.strip("\n")
            nuevas.append(("%s\n" % (re.sub(r"^ {4}", "", comentario, flags=re.M)) if comentario else "") +
                          "static void %s(%s) {\n%s\n}\n" % (nombre, firma, bloque))
            # La llamada conserva el espacio previo (lineas en blanco) y la
            # sangria del bloque que reemplaza.
            previo = pendiente + hueco
            blanco = re.match(r"\s*", previo).group(0)
            saltos = blanco[:blanco.rfind("\n") + 1] if "\n" in blanco else "\n"
            sangria = hueco.split("\n")[-1] if "\n" in hueco else "    "
            cuerpo_nuevo.append(saltos + sangria +
                                "%s(%s);" % (nombre, ", ".join(d["nombre"] for d in params)))
        else:
            cuerpo_nuevo.append(pendiente + hueco + trozo)
            d = declaracion(trozo) if tipo == "bloque" else None
            if d:
                declaradas.append(d)
        pendiente = ""
        cursor = b
    cuerpo_nuevo.append(pendiente + cuerpo[cursor:])

    if not nuevas:
        print("No hay secciones extraibles.")
        return 1
    # Las funciones nuevas van antes de la funcion original (despues de su
    # comentario de cabecera, que se queda con ella).
    inicio_def = texto.rfind("\n", 0, m.start()) + 1
    while True:
        previo = texto.rfind("\n", 0, inicio_def - 1) + 1
        linea = texto[previo:inicio_def].strip()
        if linea.startswith("//") and previo > 0:
            inicio_def = previo
        else:
            break
    resultado = (texto[:inicio_def] + "\n".join(nuevas) + "\n" + texto[inicio_def:ini_cuerpo] +
                 "".join(cuerpo_nuevo) + texto[fin_cuerpo:])
    print("%d secciones extraidas de %s; %d lineas -> %d" % (
        len(nuevas), funcion, texto.count("\n"), resultado.count("\n")))
    if aplicar:
        open(ruta, "w", encoding="utf-8").write(resultado)
    return 0


if __name__ == "__main__":
    sys.exit(main())
