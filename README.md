# ModularizadorCpp

Divide archivos C++ grandes en varias unidades de compilación de a lo sumo
N líneas (450 por defecto) **sin cambiar el código**: cada definición se mueve
entera y una verificación compara, línea por línea, el resultado contra el
original en git.

Nació para FunshiEngineOpenGL, donde dividió 23 archivos del motor y 11 suites
de test (de hasta 2900 líneas) con el build y los tests en verde.

## Requisitos

- Python 3.8+
- git (la verificación lee el original de `HEAD`: el archivo tiene que estar
  commiteado antes de dividirlo)

## Uso rápido

```bash
# Ver el plan (no escribe nada)
python3 modularizar.py src/Escena/Escena.cpp

# Aplicar, verificar y registrar las partes en el CMake
python3 modularizar.py src/Escena/Escena.cpp --aplicar --cmake CMakeLists.txt

# Suites de test (funciones libres repartidas y declaradas en el header)
python3 modularizar.py tests/EscenaTests.cpp --tests --aplicar --cmake CMakeLists.txt
python3 modularizar.py tests/OtraTests.cpp --tests --abrir-anonimos --aplicar
```

Sin `--spec`, los cortes se eligen solos: se acumulan definiciones hasta el
tamaño objetivo y las partes se llaman `<Base>2.cpp`, `<Base>3.cpp`, ...

Para nombres con significado, usar un spec:

```json
{"detalle": "EscenaDetalle.h",
 "partes": [
   {"archivo": "EscenaCamaras.cpp", "desde": "Escena::camaraActiva"},
   {"archivo": "EscenaGUI.cpp", "desde": "Escena::dibujarGUI"},
   {"archivo": "EscenaServicios.cpp", "auxiliares": ["servicios\\w+"]}
 ],
 "compartidas": ["SRC_\\w+"]}
```

- `desde`: primera definición (en orden de aparición) que pasa a esa parte;
  todo lo anterior al primer corte queda en el archivo original.
- `auxiliares`: funciones auxiliares (de namespace anónimo o `static`) que se
  mandan a esa parte con enlace externo; su declaración va al header interno.
- `compartidas`: variables (por ejemplo `const char* X = "..."`, cuyo puntero
  no es constante) que deben tener una sola instancia.

### Funciones más grandes que el límite

Si una sola función pasa el límite y está hecha de secciones `{ ... }` (lo
típico en un `main` de tests), primero se separan sus secciones en funciones:

```bash
python3 modularizador/partir_funcion.py tests/EscenaTests.cpp main --aplicar
# compilar, correr los tests y commitear; despues:
python3 modularizar.py tests/EscenaTests.cpp --tests --aplicar --cmake CMakeLists.txt
```

Cada sección pasa a `static void seccionNN(...)` y recibe por referencia las
variables de la función original que usa (`const T&` si eran `const`). El
bloque se copia con sus llaves, así lo que declara sigue en un ámbito anidado.
No se extrae una sección que haga `return`/`break`/`continue` fuera de una
lambda ni una que use variables declaradas con `auto`.

Herramientas sueltas: `modularizador/dividir.py <archivo> --listar` muestra las
definiciones de primer nivel con su línea y tamaño, y
`modularizador/verificar.py <original> <archivos...>` repite la verificación.

## Qué hace con cada cosa

| Elemento del original | Resultado |
|-----------------------|-----------|
| Includes, macros, `using`, constantes | header interno `<Base>Detalle.h`, que incluyen todas las partes |
| Métodos y funciones libres | se reparten por posición, enteros, con sus comentarios |
| Namespaces con nombre | se abren y cada parte vuelve a envolver su contenido |
| Auxiliar sin estado que usa una sola parte | va a esa parte (enlace interno) |
| Auxiliar sin estado que usan varias partes | queda en el header (una copia por unidad, como siempre fue) |
| Auxiliar **con estado** (`static` locales) | una sola definición en el original, declarada en el header |
| Variables globales o de namespace anónimo | una sola definición en el original, `extern` en `<Base>Detalle` |
| Tipos de un namespace anónimo compartidos | namespace con nombre `<Base>Detalle` (un tipo anónimo es distinto en cada unidad) |
| Definiciones de miembros estáticos (`T C::x = v;`) | una sola parte |
| Lo que esté dentro de `#if` / `#ifdef` | no se mueve: queda en su lugar en el header |

## Garantías y límites

- **Mismo código**: la verificación compara el multiconjunto de líneas (sin
  blancos ni includes) y solo admite las diferencias que la herramienta mete a
  propósito: prototipos, envoltorios de namespace, `static` quitado y valores
  por defecto movidos a la declaración.
- No reordena el código dentro de una definición ni toca su contenido.
- **No compila por vos**: después de dividir hay que compilar y correr los
  tests. Errores típicos que aparecen al compilar y la herramienta ya evita:
  estado duplicado por unidad, definiciones repetidas, tipos anónimos
  compartidos.
- Rechaza (y pide hacerlo a mano) los archivos con condicionales de
  preprocesador entre definiciones de primer nivel, y las funciones con estado
  dentro de un `#if`.
- Una sola función más grande que el límite no se puede dividir: hay que
  partirla a mano (por ejemplo, en funciones auxiliares) y volver a correr.
- Los nombres de las auxiliares que pasan a enlace externo no deben chocar con
  otros símbolos del mismo binario; si chocan, el enlazador lo avisa.

## Licencia

Apache License 2.0 (ver `LICENSE`).
