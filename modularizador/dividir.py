#!/usr/bin/env python3
"""Divide un .cpp grande en varias unidades sin cambiar la logica.

Uso:
    python3 modularizador/dividir.py <archivo.cpp> <spec.json> [--aplicar]
    python3 modularizador/dividir.py <archivo.cpp> --listar

spec.json:
    {"detalle": "GameSceneDetalle.h",
     "partes": [{"archivo": "GameSceneCamaras.cpp", "desde": "GameScene::pintarVentanaCamaras"}, ...]}

Cada "desde" es el nombre de la primera definicion (por orden de aparicion) que
pasa a esa parte; todo lo anterior al primer corte queda en el archivo original.

Reglas:
- Se divide por definiciones completas de primer nivel (funciones, metodos).
- El preambulo (includes, using, constantes y funciones auxiliares, bloques de
  namespace anonimo) va a un header interno <detalle> que incluyen todas las
  partes. Las funciones auxiliares quedan con enlace interno (una copia por
  unidad, igual que antes).
- Las VARIABLES mutables de enlace interno (static T x; o dentro de un namespace
  anonimo) no pueden duplicarse: se declaran 'extern' en el header, dentro de
  un namespace con nombre, y se definen una sola vez en el archivo original.
- Los comentarios pegados a una definicion se mueven con ella.
"""
import json
import os
import re
import sys

LICENCIA_FIN = "*/"


def tokens_nivel(texto):
    """Devuelve una lista de (inicio, fin, tipo) de los items de primer nivel.

    tipo: 'pp' (linea de preprocesador), 'bloque' (termina en } de nivel 0 o en ;),
    'coment'. Respeta comentarios, cadenas, caracteres y raw strings.
    """
    items = []
    i = 0
    n = len(texto)
    inicio = None
    profundidad = 0

    def saltar_espacio(k):
        while k < n and texto[k] in " \t\r\n":
            k += 1
        return k

    while i < n:
        if inicio is None:
            i = saltar_espacio(i)
            if i >= n:
                break
            # comentarios sueltos de primer nivel
            if texto.startswith("//", i):
                fin = texto.find("\n", i)
                fin = n if fin < 0 else fin + 1
                items.append((i, fin, "coment"))
                i = fin
                continue
            if texto.startswith("/*", i):
                fin = texto.find("*/", i) + 2
                items.append((i, fin, "coment"))
                i = fin
                continue
            if texto[i] == "#":
                fin = i
                while True:
                    eol = texto.find("\n", fin)
                    if eol < 0:
                        eol = n
                        break
                    if texto[eol - 1] == "\\":
                        fin = eol + 1
                        continue
                    break
                items.append((i, min(eol + 1, n), "pp"))
                i = eol + 1
                continue
            inicio = i
            profundidad = 0
        c = texto[i]
        if texto.startswith("//", i):
            fin = texto.find("\n", i)
            i = n if fin < 0 else fin
            continue
        if texto.startswith("/*", i):
            i = texto.find("*/", i) + 2
            continue
        m = re.match(r'R"([^(\s]*)\(', texto[i:i + 40])
        if m and (i == 0 or not (texto[i - 1].isalnum() or texto[i - 1] == "_")):
            delim = ")" + m.group(1) + '"'
            i = texto.find(delim, i + len(m.group(0))) + len(delim)
            continue
        if c == '"' or c == "'":
            j = i + 1
            while j < n and texto[j] != c:
                if texto[j] == "\\":
                    j += 1
                j += 1
            i = j + 1
            continue
        if c == "{":
            profundidad += 1
        elif c == "}":
            profundidad -= 1
            if profundidad == 0:
                # Un } de nivel 0 cierra el item salvo que siga un ';' (struct, lambda asignada)
                k = saltar_espacio(i + 1)
                fin = k + 1 if k < n and texto[k] == ";" else i + 1
                items.append((inicio, fin, "bloque"))
                inicio = None
                i = fin
                continue
        elif c == ";" and profundidad == 0:
            items.append((inicio, i + 1, "bloque"))
            inicio = None
        i += 1
    return items


def nombre_definicion(fragmento):
    """Nombre calificado de la funcion definida (o None)."""
    cabecera = fragmento.split("{", 1)[0]
    cabecera = re.sub(r"//[^\n]*", "", cabecera)
    m = re.findall(r"([A-Za-z_~][\w:~]*(?:operator\s*[^\s(]+)?)\s*\(", cabecera)
    if not m:
        return None
    return m[0].split("<")[0]


def es_namespace_anonimo(fragmento):
    return re.match(r"\s*namespace\s*\{", fragmento) is not None


def es_variable_mutable(fragmento):
    f = re.sub(r"//[^\n]*|/\*.*?\*/", "", fragmento, flags=re.S).strip()
    if not f.endswith(";") or "{" in f.split("=")[0] and "(" not in f.split("=")[0]:
        pass
    primera = f.split("=", 1)[0]
    if re.match(r"(using|typedef|namespace|struct|class|enum|template|extern|friend)\b", f):
        return False
    if re.search(r"\b(constexpr|const)\b", primera):
        return False
    if "(" in primera and "=" not in f:
        return False  # prototipo
    if "(" in primera and not re.search(r"\)\s*$", primera):
        pass
    if "(" in primera:
        # 'static Foo x(arg);' es variable; 'static void f(int);' es prototipo:
        # se distingue porque los argumentos del prototipo llevan tipos.
        return False
    return f.endswith(";")


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    ruta = sys.argv[1]
    texto = open(ruta, encoding="utf-8").read().lstrip("\ufeff")  # sin BOM
    items = tokens_nivel(texto)

    # Asocia comentarios a la definicion siguiente. Los namespaces con nombre
    # se abren: sus items quedan marcados con el namespace (campo "ns").
    defs = []

    profundidad_pp = [0]

    fin_namespace = [None]

    def aplanar(base, cuerpo, ns):
        pendiente = None
        for ini, fin, tipo in tokens_nivel(cuerpo):
            a, b = base + ini, base + fin
            frag = texto[a:b]
            if tipo == "coment":
                # "}  // namespace X" al cerrar un namespace con nombre: el
                # cierre se regenera, el comentario no se copia.
                if fin_namespace[0] is not None and pendiente is None and \
                        "\n" not in texto[fin_namespace[0]:a]:
                    fin_namespace[0] = None
                    continue
                ultimo = defs[-1] if defs else None
                if ultimo and ultimo["ns"] == ns and pendiente is None and \
                        "\n" not in texto[ultimo["fin"]:a]:
                    ultimo["fin"] = b
                    continue
                if pendiente is None:
                    pendiente = a
                continue
            # Modo tests: el namespace anonimo se abre como si no estuviera (sus
            # funciones se reparten por posicion y pasan a enlace externo).
            if tipo == "bloque" and spec_previa.get("abrir_anonimos") and es_namespace_anonimo(frag):
                abre = frag.index("{") + 1
                cierra = frag.rindex("}")
                if pendiente is not None:
                    defs.append({"ini": pendiente, "fin": a, "tipo": "coment_suelto", "nombre": None,
                                 "frag": texto[pendiente:a], "ns": ns, "cond": profundidad_pp[0] > 0})
                aplanar(a + abre, frag[abre:cierra], ns)
                fin_namespace[0] = b
                pendiente = None
                continue
            m = re.match(r"\s*namespace\s+([\w:]+)\s*\{", frag) if tipo == "bloque" else None
            if m:
                abre = frag.index("{") + 1
                cierra = frag.rindex("}")
                ns_interno = (ns + "::" if ns else "") + m.group(1)
                if pendiente is not None:
                    # El comentario que precede al namespace se conserva como
                    # item propio dentro de ese namespace.
                    defs.append({"ini": pendiente, "fin": a, "tipo": "coment_suelto", "nombre": None,
                                 "frag": texto[pendiente:a], "ns": ns_interno,
                                 "cond": profundidad_pp[0] > 0})
                aplanar(a + abre, frag[abre:cierra], ns_interno)
                fin_namespace[0] = b
                pendiente = None
                continue
            nombre = nombre_definicion(frag) if tipo == "bloque" else None
            if tipo == "pp":
                if re.match(r"\s*#\s*if", frag):
                    profundidad_pp[0] += 1
                elif re.match(r"\s*#\s*endif", frag):
                    profundidad_pp[0] -= 1
            defs.append({"ini": pendiente if pendiente is not None else a, "fin": b,
                         "tipo": tipo, "nombre": nombre, "frag": frag, "ns": ns,
                         "cond": profundidad_pp[0] > 0})
            pendiente = None

    spec_previa = {}
    if len(sys.argv) > 2 and sys.argv[2] != "--listar":
        spec_previa = json.load(open(sys.argv[2], encoding="utf-8"))
    aplanar(0, texto, "")

    if sys.argv[2] == "--listar":
        for d in defs:
            lineas = texto[d["ini"]:d["fin"]].count("\n") + 1
            etiqueta = d["nombre"] or ("<" + d["tipo"] + ">")
            if d["ns"]:
                etiqueta = d["ns"] + " :: " + etiqueta
            print("%5d  %4d  %s" % (texto[:d["ini"]].count("\n") + 1, lineas, etiqueta))
        return 0

    spec = json.load(open(sys.argv[2], encoding="utf-8"))
    aplicar = "--aplicar" in sys.argv
    carpeta = os.path.dirname(ruta)
    base = os.path.splitext(os.path.basename(ruta))[0]
    detalle = spec.get("detalle", base + "Detalle.h")
    ns_detalle = re.sub(r"\W", "", base) + "Detalle"

    # Primer metodo/funcion "real": desde ahi empiezan las definiciones a repartir.
    # El preambulo son los items anteriores mas los auxiliares de cualquier lugar.
    def es_auxiliar(d):
        if d["tipo"] == "pp":
            return True
        f = re.sub(r"^(\s*(//[^\n]*|/\*.*?\*/))*\s*", "", d["frag"], flags=re.S)
        if es_namespace_anonimo(f):
            return True
        # Modo tests: las funciones 'static' de primer nivel se reparten por
        # posicion como cualquier definicion (pasan a enlace externo).
        if spec.get("externalizar_static") and f.startswith("static ") and \
                d["frag"].rstrip().endswith("}") and d["nombre"] and "::" not in d["nombre"] and \
                not re.match(r"static\s+(constexpr|const)\b", f) and "(" in f.split("{")[0] and \
                not re.match(r"static\s+(struct|class|union|enum)\b", f):
            return False
        # Definicion de un miembro estatico: "Tipo Clase::miembro = valor;".
        if not d["frag"].rstrip().endswith("}") and \
                re.match(r"[^=;(]*\b[A-Za-z_]\w*::[A-Za-z_]\w*\s*(=|;|\{|\[)", f) and \
                not re.match(r"(using|typedef|extern|namespace|struct|class|union|enum)\b", f):
            return False
        # Definiciones de miembros (Clase::algo), incluso "= default;".
        if d["nombre"] and "::" in d["nombre"] and not re.match(r"(using|typedef)\b", f):
            return False
        if re.match(r"(static|using|typedef|namespace\s+\w+\s*=|struct|class|enum|template|constexpr|inline)\b", f):
            return True
        # Funcion libre con enlace externo (declarada en un header): es una
        # definicion y va a su parte.
        if d["frag"].rstrip().endswith("}") and d["nombre"]:
            return False
        return True  # declaraciones sueltas, prototipos

    # Corte automatico: se acumulan definiciones hasta 'auto_max' lineas y se
    # abre una parte nueva (<Base>2.cpp, <Base>3.cpp, ...).
    if spec.get("auto_max"):
        limite = spec["auto_max"]
        acumulado, n_parte = 0, 1
        spec["partes"] = []
        for d in defs:
            if es_auxiliar(d) or not d["nombre"]:
                continue
            tam = texto[d["ini"]:d["fin"]].count("\n") + 2
            if acumulado and acumulado + tam > limite:
                n_parte += 1
                spec["partes"].append({"archivo": "%s%d.cpp" % (base, n_parte), "desde": d["nombre"]})
                acumulado = 0
            acumulado += tam

    primera_def = next((i for i, d in enumerate(defs) if not es_auxiliar(d)), len(defs))
    for d in defs[primera_def:]:
        if d["tipo"] == "pp" and re.match(r"\s*#\s*(if|ifdef|ifndef|else|elif|endif)\b", d["frag"]):
            print("ERROR: hay condicionales de preprocesador entre las definiciones "
                  "(linea %d): dividir a mano." % (texto[:d["ini"]].count("\n") + 1))
            return 1

    cortes = [p.get("desde") for p in spec["partes"]]
    parte_de = {}
    actual = 0
    for idx, d in enumerate(defs):
        if d["nombre"] and d["nombre"] in cortes and not es_auxiliar(d):
            actual = cortes.index(d["nombre"]) + 1
        parte_de[idx] = actual

    faltan = [c for c in cortes if c and c not in [d["nombre"] for d in defs]]
    if faltan:
        print("ERROR: cortes no encontrados:", faltan)
        return 1

    def firma_simple(texto_fn):
        codigo = re.sub(r"^(\s*(//[^\n]*\n|/\*.*?\*/))*\s*", "", texto_fn, flags=re.S)
        codigo = re.sub(r"^static\s+", "", codigo)
        prof = 0
        for pos, ch in enumerate(codigo):
            if ch == "(":
                prof += 1
            elif ch == ")":
                prof -= 1
            elif ch == "{" and prof == 0:
                return re.sub(r"\s+", " ", codigo[:pos]).strip() + ";"
        return None

    firma = firma_simple

    def quitar_defaults(texto_fn):
        # Quita "= valor" de los parametros de la definicion (el valor por
        # defecto queda solo en el prototipo del header).
        inicio_codigo = len(texto_fn) - len(
            re.sub(r"^(\s*(//[^\n]*\n|/\*.*?\*/))*\s*", "", texto_fn, flags=re.S))
        abre = texto_fn.find("(", inicio_codigo)
        if abre < 0:
            return texto_fn
        prof, i, salida, saltando = 0, abre, [], False
        while i < len(texto_fn):
            ch = texto_fn[i]
            if ch in "([{<":
                prof += 1
            elif ch in ")]}>":
                prof -= 1
                if prof == 0:
                    return texto_fn[:abre] + "".join(salida) + texto_fn[i:]
            if prof == 1 and ch == "=" and not saltando:
                saltando = True
                while salida and salida[-1] == " ":
                    salida.pop()
            elif prof == 1 and ch == "," and saltando:
                saltando = False
            if not saltando:
                salida.append(ch)
            i += 1
        return texto_fn

    prototipos_libres = []
    preambulo = []
    variables = []
    partes = [[] for _ in range(len(cortes) + 1)]
    for idx, d in enumerate(defs):
        frag = texto[d["ini"]:d["fin"]]
        if es_auxiliar(d):
            preambulo.append((d, frag))
        else:
            nombre_d = d["nombre"] or ""
            codigo_d = re.sub(r"^(\s*(//[^\n]*\n|/\*.*?\*/))*\s*", "", frag, flags=re.S)
            if parte_de[idx] != 0 and nombre_d and "::" not in nombre_d and nombre_d != "main" \
                    and frag.rstrip().endswith("}") and not codigo_d.startswith("template"):
                # Funcion libre que pasa a otra unidad: se declara en el header.
                frag = re.sub(r"^((\s*(//[^\n]*\n|/\*.*?\*/))*\s*)static\s+", r"\1", frag, flags=re.S)
                prototipos_libres.append((d["ns"], firma(frag)))
                frag = quitar_defaults(frag)
            elif parte_de[idx] == 0 and nombre_d and "::" not in nombre_d and \
                    spec.get("externalizar_static") and codigo_d.startswith("static ") and \
                    frag.rstrip().endswith("}"):
                # Se queda en el original pero otras unidades pueden llamarla.
                frag = re.sub(r"^((\s*(//[^\n]*\n|/\*.*?\*/))*\s*)static\s+", r"\1", frag, flags=re.S)
                prototipos_libres.append((d["ns"], firma(frag)))
                frag = quitar_defaults(frag)
            elif parte_de[idx] == 0 and nombre_d and "::" not in nombre_d and nombre_d != "main" \
                    and frag.rstrip().endswith("}") and not codigo_d.startswith("template") \
                    and spec.get("prototipos_original"):
                prototipos_libres.append((d["ns"], firma(frag)))
                frag = quitar_defaults(frag)
            partes[parte_de[idx]].append((d["ns"], frag, False))

    # Auxiliares de enlace interno (funciones y variables de namespaces anonimos
    # y 'static' libres): cada una va a la UNICA parte que la usa; las que usan
    # varias partes (o que usa otra auxiliar que queda compartida) van al header.
    unidades = []  # {"ns", "anon", "texto", "nombre", "clase": fn|var|otro}
    for d, frag in preambulo:
        f = frag.lstrip()
        sin_coment = re.sub(r"^(\s*(//[^\n]*\n|/\*.*?\*/))*\s*", "", f, flags=re.S)
        if es_namespace_anonimo(sin_coment) and d["tipo"] == "bloque":
            encabezado = f[:len(f) - len(sin_coment)]
            cuerpo = sin_coment[sin_coment.index("{") + 1:sin_coment.rindex("}")]
            pendiente = encabezado

            prof_local = [0]

            def unidades_anon(cuerpo_anon, pendiente_anon):
                fin_previo = None
                for a, b, t in tokens_nivel(cuerpo_anon):
                    trozo = cuerpo_anon[a:b]
                    if t == "coment" and fin_previo is not None and not pendiente_anon and \
                            cuerpo_anon[fin_previo - 1] != "\n" and \
                            "\n" not in cuerpo_anon[fin_previo:a] and unidades:
                        # comentario al final de la misma linea: va con su item
                        unidades[-1]["texto"] += cuerpo_anon[fin_previo:b].rstrip("\n")
                        fin_previo = b
                        continue
                    fin_previo = b
                    if t == "pp":
                        if re.match(r"\s*#\s*if", trozo):
                            prof_local[0] += 1
                        elif re.match(r"\s*#\s*endif", trozo):
                            prof_local[0] -= 1
                    if t == "coment":
                        pendiente_anon += trozo if trozo.endswith("\n") else trozo + "\n"
                        continue
                    if t == "bloque" and es_namespace_anonimo(trozo):
                        # namespace anonimo anidado: es el mismo espacio, se aplana.
                        unidades_anon(trozo[trozo.index("{") + 1:trozo.rindex("}")], pendiente_anon)
                        pendiente_anon = ""
                        continue
                    clase = "otro"
                    nom = None
                    if t == "bloque" and trozo.rstrip().endswith("}") and "(" in trozo.split("{")[0] \
                            and not re.match(r"\s*(struct|class|enum|union)\b", trozo):
                        clase, nom = "fn", nombre_definicion(trozo)
                    elif t == "bloque" and es_variable_mutable(trozo):
                        clase = "var"
                        m = re.search(r"([A-Za-z_]\w*)\s*(\[[^\]]*\])?\s*(=|\{|;)", trozo)
                        nom = m.group(1) if m else None
                    unidades.append({"ns": d["ns"], "anon": True, "texto": pendiente_anon + trozo,
                                     "nombre": nom, "clase": clase,
                                     "cond": d.get("cond") or prof_local[0] > 0 or t == "pp"})
                    pendiente_anon = ""
                if pendiente_anon.strip():
                    # comentario suelto al final del namespace: se conserva
                    unidades.append({"ns": d["ns"], "anon": True, "texto": pendiente_anon.rstrip("\n"),
                                     "nombre": None, "clase": "otro"})

            unidades_anon(cuerpo, pendiente)
        elif d["tipo"] == "bloque" and f.startswith("static ") and frag.rstrip().endswith("}") \
                and d["nombre"]:
            unidades.append({"ns": d["ns"], "anon": False, "texto": frag, "nombre": d["nombre"],
                             "clase": "fn", "cond": d.get("cond")})
        else:
            unidades.append({"ns": d["ns"], "anon": False, "texto": frag, "nombre": None,
                             "clase": "otro", "d": d})

    textos_partes = ["\n".join(e[1] for e in p) for p in partes]
    destino = {i: None for i in range(len(unidades))}  # None = header

    def usa(texto_buscar, nombre):
        return re.search(r"(?<![\w.>])" + re.escape(nombre) + r"\b", texto_buscar) is not None

    # Variables que el spec pide compartir con una sola instancia (definicion en
    # el original, 'extern' en el header) aunque sean 'const T*' (el puntero
    # no es constante: es una variable como cualquier otra).
    for patron in spec.get("compartidas", []):
        for u in unidades:
            if u["anon"] and u["clase"] == "otro":
                m = re.match(r"\s*(?://[^\n]*\n|/\*.*?\*/|\s)*[^=;(]*?\b([A-Za-z_]\w*)\s*=", u["texto"], re.S)
                if m and re.fullmatch(patron, m.group(1)):
                    u["clase"] = "var"
                    u["nombre"] = m.group(1)
                    u["fija"] = True

    # Auxiliares reclamadas por patron en el spec: pasan a esa parte con
    # enlace externo (se declaran en el header interno).
    externos = set()
    for k, p in enumerate(spec["partes"]):
        for patron in p.get("auxiliares", []):
            for i, u in enumerate(unidades):
                if u["clase"] == "fn" and u["nombre"] and not u.get("cond") and \
                        i not in externos and re.fullmatch(patron, u["nombre"].split("::")[-1]):
                    destino[i] = k + 1
                    externos.add(i)

    cambio = True
    while cambio:
        cambio = False
        for i, u in enumerate(unidades):
            if destino[i] is not None or u["clase"] not in ("fn", "var") or not u["nombre"] \
                    or u.get("cond") or u.get("fija"):
                continue
            nombre = u["nombre"].split("::")[-1]
            usuarios = {k for k, t in enumerate(textos_partes) if usa(t, nombre)}
            bloqueado = False
            for j, v in enumerate(unidades):
                if j == i or not usa(v["texto"], nombre):
                    continue
                if destino[j] is None:
                    bloqueado = True
                else:
                    usuarios.add(destino[j])
            if not bloqueado and len(usuarios) == 1:
                destino[i] = usuarios.pop()
                cambio = True

    # Una auxiliar con estado propio (variables 'static' locales) no puede
    # quedar copiada en cada unidad: cada copia tendria su propio estado. Si
    # quedo compartida, se define una sola vez en el original (enlace externo).
    for i, u in enumerate(unidades):
        if destino[i] is None and u["clase"] == "fn" and u["nombre"] and \
                re.search(r"\n\s+(static|thread_local)\s+(?!_cast|_assert)", u["texto"]):
            if u.get("cond"):
                print("ERROR: '%s' tiene estado static y esta dentro de un #if: dividir a mano."
                      % u["nombre"])
                sys.exit(1)
            destino[i] = 0
            externos.add(i)

    def firma(texto_fn):
        codigo = re.sub(r"^(\s*(//[^\n]*\n|/\*.*?\*/))*\s*", "", texto_fn, flags=re.S)
        codigo = re.sub(r"^static\s+", "", codigo)
        prof = 0
        for pos, ch in enumerate(codigo):
            if ch == "(":
                prof += 1
            elif ch == ")":
                prof -= 1
            elif ch == "{" and prof == 0:
                return re.sub(r"\s+", " ", codigo[:pos]).strip() + ";"
        return None

    prototipos = []  # (ns, firma)
    movidas = [[] for _ in partes]
    for i, u in enumerate(unidades):
        if destino[i] is not None:
            texto_u = u["texto"].strip("\n")
            if i in externos:
                texto_u = re.sub(r"^((\s*(//[^\n]*\n|/\*.*?\*/))*\s*)static\s+", r"\1", texto_u, flags=re.S)
                prototipos.append((u["ns"], firma(u["texto"])))
            movidas[destino[i]].append((u["ns"], texto_u, u["anon"] and i not in externos))
    for k in range(len(partes)):
        partes[k] = movidas[k] + partes[k]

    # Lo que queda compartido va al header. Dentro de cada namespace original:
    # los TIPOS de los namespaces anonimos pasan a un namespace con nombre
    # (<Base>Detalle) porque un tipo anonimo es distinto en cada unidad y no
    # puede compartirse; las funciones y constantes siguen en un namespace
    # anonimo (una copia por unidad, como antes). Las variables mutables se
    # declaran 'extern' en <Base>Detalle y se definen una vez en el original.
    def declaracion_extern(v):
        v = re.sub(r"^(\s*(//[^\n]*\n|/\*.*?\*/))*\s*", "", v, flags=re.S).strip()
        decl = re.sub(r"\s*(=.*)?;$", ";", v, flags=re.S)
        decl = re.sub(r"\s*\{[^}]*\};$", ";", decl)
        return "extern " + decl

    RE_TIPO = re.compile(r"(struct|class|enum|union|using|typedef|template\s*<[^>]*>\s*(struct|class))\b")
    entradas_header = []  # (ns, envoltorio: ''|'anon'|'detalle', texto, tipo)
    variables = []        # (ns, texto)
    for i, u in enumerate(unidades):
        if i in externos:
            # El prototipo va donde estaba la funcion: lo que la usa en el
            # header (y lo que ella usa) mantiene el orden original.
            entradas_header.append((u["ns"], "", firma(u["texto"]), "proto"))
            continue
        if destino[i] is not None:
            continue
        codigo = re.sub(r"^(\s*(//[^\n]*\n|/\*.*?\*/))*\s*", "", u["texto"], flags=re.S)
        d = u.get("d") or {"tipo": "bloque"}
        if u["anon"]:
            if u["clase"] == "var":
                variables.append((u["ns"], u["texto"]))
                entradas_header.append((u["ns"], "detalle", declaracion_extern(u["texto"]), "proto"))
            elif RE_TIPO.match(codigo):
                entradas_header.append((u["ns"], "detalle", u["texto"].strip("\n"), "bloque"))
            else:
                entradas_header.append((u["ns"], "anon", u["texto"].strip("\n"), "bloque"))
        elif d["tipo"] == "bloque" and codigo.startswith("static ") and \
                not codigo.rstrip().endswith("}") and es_variable_mutable(codigo):
            variables.append((u["ns"], codigo[len("static "):]))
            entradas_header.append((u["ns"], "detalle", declaracion_extern(codigo[len("static "):]), "proto"))
        elif d["tipo"] == "bloque" and not codigo.rstrip().endswith("}") and \
                not re.match(r"(static|extern|using|typedef|namespace|struct|class|enum|union|template)\b", codigo) \
                and es_variable_mutable(codigo):
            variables.append((u["ns"], u["texto"]))
            entradas_header.append((u["ns"], "detalle", declaracion_extern(u["texto"]), "proto"))
        else:
            entradas_header.append((u["ns"], "", u["texto"].strip("\n"), d["tipo"]))

    licencia = texto[:texto.index(LICENCIA_FIN) + 2] if LICENCIA_FIN in texto[:2000] else ""
    guardia = re.sub(r"\W", "_", detalle).upper()

    def cierre_envoltorio(env):
        if env == "anon":
            return "}  // namespace"
        if env == "detalle":
            return "}  // namespace %s\nusing namespace %s;" % (ns_detalle, ns_detalle)
        return ""

    def apertura_envoltorio(env):
        return {"anon": "namespace {", "detalle": "namespace %s {" % ns_detalle}.get(env, "")

    def componer(entradas):
        salida = []
        ns_abierto, env_abierto, tipo_previo = "", "", None
        for ns_h, env, h, tipo_h in entradas:
            if licencia and h.startswith(licencia.strip()):
                h = h[len(licencia.strip()):].strip("\n")
            if not h.strip():
                continue
            if ns_h != ns_abierto or env != env_abierto:
                if env_abierto:
                    salida.append(cierre_envoltorio(env_abierto))
                if ns_h != ns_abierto:
                    if ns_abierto:
                        salida.append("}  // namespace " + ns_abierto)
                    if ns_h:
                        salida.append("namespace " + ns_h + " {")
                    ns_abierto = ns_h
                if env:
                    salida.append(apertura_envoltorio(env))
                env_abierto = env
                tipo_previo = None
            if tipo_h in ("pp", "proto") and tipo_previo == tipo_h and salida:
                salida[-1] += "\n" + h
            else:
                salida.append(h)
            tipo_previo = tipo_h
        if env_abierto:
            salida.append(cierre_envoltorio(env_abierto))
        if ns_abierto:
            salida.append("}  // namespace " + ns_abierto)
        return "\n\n".join(salida)

    extras = []
    if prototipos_libres:
        extras.append(("", "", "// Funciones repartidas entre las unidades.", "coment"))
        for ns_p, fp in prototipos_libres:
            extras.append((ns_p, "", fp, "proto"))
    cuerpo_header = componer(entradas_header + extras)
    definiciones = [(ns_v, "detalle", v.strip(), "proto") for ns_v, v in variables]

    texto_header = (licencia + "\n" if licencia else "") + \
        "// Preambulo compartido por las unidades de %s (generado al dividirlo).\n" % (base + ".cpp") + \
        "#ifndef %s\n#define %s\n\n" % (guardia, guardia) + cuerpo_header + "\n\n#endif\n"

    salidas = {}
    salidas[os.path.join(carpeta, detalle)] = texto_header
    cab = (licencia + "\n" if licencia else "") + '#include "%s"\n\n' % detalle
    original = cab
    if definiciones:
        original += componer(definiciones) + "\n\n"
    def emitir(lista):
        return componer([(e[0], "anon" if e[2] else "", e[1], "bloque") for e in lista]) + "\n"

    original += emitir(partes[0])
    salidas[ruta] = original
    for k, p in enumerate(spec["partes"]):
        salidas[os.path.join(carpeta, p["archivo"])] = cab + emitir(partes[k + 1])

    for r, t in salidas.items():
        print("%5d lineas  %s" % (t.count("\n"), os.path.relpath(r)))
    if variables:
        print("variables con una sola instancia:", len(variables))
    if aplicar:
        for r, t in salidas.items():
            open(r, "w", encoding="utf-8").write(t)
    return 0


if __name__ == "__main__":
    sys.exit(main())
