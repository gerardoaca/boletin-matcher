"""Motor de matching determinista con doble llave y dedup por expediente+hoja."""
from dataclasses import dataclass, field
from .extractor import HojaBoletin, localizar_expedientes_en_hoja
from .normalizer import (
    normalizar_texto,
    alguna_parte_en_texto,
    alguna_parte_junta_en_texto,
    juzgados_equivalentes,
)
from .listado_loader import RegistroCliente


@dataclass
class Coincidencia:
    expediente: str
    actor_listado: str
    cliente: str
    juzgado_listado: str
    hoja: int                    # índice PDF (PyMuPDF)
    pagina_impresa: str          # número de página impreso en el boletín
    linea_inicio: int
    linea_fin: int
    bloque_texto: str
    ruta_validacion: str
    motivo: str = ""
    fila_listado: int = 0
    extras: dict = field(default_factory=dict)


def _index_por_expediente(listado: list[RegistroCliente]):
    idx = {}
    for r in listado:
        idx.setdefault(r.expediente, []).append(r)
    return idx


def _juzgado_en_pagina(juzgado_norm: str, hoja: HojaBoletin, juzgado_seccion: str) -> bool:
    """¿El juzgado del listado aparece en la sección o en cualquier header de la hoja?"""
    if not juzgado_norm:
        return False
    objetivos = [juzgado_seccion] + hoja.juzgados_pagina
    for j in objetivos:
        if not j:
            continue
        if juzgados_equivalentes(juzgado_norm, normalizar_texto(j)):
            return True
    return False


def buscar_coincidencias(
    hojas: list[HojaBoletin],
    listado: list[RegistroCliente],
) -> tuple[list[Coincidencia], list[Coincidencia]]:
    idx = _index_por_expediente(listado)
    validadas: list[Coincidencia] = []
    revision: list[Coincidencia] = []
    vistos: set[tuple] = set()  # dedup: (expediente, fila_listado, hoja)

    for hoja in hojas:
        bloques = localizar_expedientes_en_hoja(hoja)
        for bloque in bloques:
            exp = bloque.expediente_match
            if exp not in idx:
                continue
            bloque_norm = normalizar_texto(bloque.texto)
            for reg in idx[exp]:
                clave = (exp, reg.fila_origen, hoja.numero)
                if clave in vistos:
                    continue
                vistos.add(clave)

                # Match por tokens (orden libre): actor del listado
                actor_en_bloque = (
                    reg.actor and not reg.actor_reservado
                    and alguna_parte_en_texto(reg.actor, bloque_norm)
                )
                cliente_en_bloque = (
                    reg.cliente
                    and alguna_parte_en_texto(reg.cliente, bloque_norm)
                )
                juzgado_match = _juzgado_en_pagina(
                    reg.juzgado, hoja, bloque.juzgado_seccion
                )

                c = Coincidencia(
                    expediente=exp,
                    actor_listado=reg.actor,
                    cliente=reg.cliente,
                    juzgado_listado=reg.juzgado,
                    hoja=bloque.hoja,
                    pagina_impresa=hoja.pagina_impresa,
                    linea_inicio=bloque.linea_inicio,
                    linea_fin=bloque.linea_fin,
                    bloque_texto=bloque.texto,
                    ruta_validacion="REVISION",
                    fila_listado=reg.fila_origen,
                    extras={
                        "juzgado_seccion": bloque.juzgado_seccion,
                        "juzgados_hoja": hoja.juzgados_pagina,
                        "actor_en_bloque": actor_en_bloque,
                        "cliente_en_bloque": cliente_en_bloque,
                        "juzgado_match": juzgado_match,
                    },
                )

                # En CDMX el mismo número de expediente existe en distintos
                # juzgados y son casos distintos. Si el listado trae juzgado y
                # NO coincide con el del bloque, degradamos a REVISION aunque
                # los tokens del actor/cliente caigan: es la firma típica de
                # un homónimo cross-juzgado.
                #
                # Importante: solo aplica si EFECTIVAMENTE detectamos un juzgado
                # en el bloque o en la hoja. Si el detector de headers no encontró
                # ninguno, no tenemos forma de comparar y no debemos descartar
                # el match (evita la regresión de mandar todos los matches reales
                # a REVISION cuando el formato del boletín cambia o el header no
                # se detecta).
                juzgado_detectado = bool(
                    bloque.juzgado_seccion or hoja.juzgados_pagina
                )
                juzgado_conflicto = (
                    bool(reg.juzgado)
                    and juzgado_detectado
                    and not juzgado_match
                )

                # Ruta A: actor del listado aparece (tokens) en el bloque
                if actor_en_bloque:
                    if juzgado_conflicto:
                        c.motivo = (
                            "Match expediente + actor (tokens), pero el juzgado "
                            f"del listado ({reg.juzgado}) NO coincide con el "
                            f"del bloque ({bloque.juzgado_seccion}). "
                            "Posible homónimo en otro juzgado."
                        )
                        revision.append(c)
                        continue
                    c.ruta_validacion = "A_actor"
                    c.motivo = (
                        "Match expediente + actor (tokens)"
                        + (" + juzgado coincide" if juzgado_match else "")
                    )
                    validadas.append(c)
                    continue

                # Ruta A2: el cliente del listado aparece como parte en el boletín
                if cliente_en_bloque:
                    if juzgado_conflicto:
                        c.motivo = (
                            "Match expediente + cliente, pero el juzgado del "
                            f"listado ({reg.juzgado}) NO coincide con el del "
                            f"bloque ({bloque.juzgado_seccion}). "
                            "Posible homónimo en otro juzgado."
                        )
                        revision.append(c)
                        continue
                    c.ruta_validacion = "A_cliente"
                    c.motivo = (
                        "Match expediente + cliente (cliente aparece como parte procesal)"
                        + (" + juzgado coincide" if juzgado_match else "")
                    )
                    validadas.append(c)
                    continue

                # Ruta B: actor reservado/genérico → exigimos juzgado coincidente
                if reg.actor_reservado:
                    if juzgado_match:
                        c.ruta_validacion = "B_juzgado"
                        c.motivo = "Actor reservado/genérico: match expediente + juzgado"
                        validadas.append(c)
                        continue
                    # Si el cliente NO está en el bloque del expediente, descartar
                    # silenciosamente: es otra sucesión homónima.
                    if not cliente_en_bloque:
                        continue
                    c.motivo = (
                        "Actor reservado/genérico, juzgado del listado NO aparece "
                        "en la sección/encabezados de la hoja"
                    )
                    revision.append(c)
                    continue

                # Ruta C: actor desconocido (celda vacía en el listado, no declarado).
                # NO exigimos juzgado obligatorio. Si el juzgado declarado del
                # listado existe y conflictúa, degradamos a REVISION (homónimo
                # cross-juzgado). Si coincide, validamos. Si no hay forma de
                # corroborar más allá del expediente, mandamos a REVISION en
                # lugar de descartar silenciosamente.
                if reg.actor_desconocido:
                    if juzgado_conflicto:
                        c.motivo = (
                            "Actor no declarado en listado, juzgado del listado "
                            f"({reg.juzgado}) NO coincide con el del bloque "
                            f"({bloque.juzgado_seccion}). Posible homónimo."
                        )
                        revision.append(c)
                        continue
                    if juzgado_match:
                        c.ruta_validacion = "B_juzgado"
                        c.motivo = (
                            "Actor no declarado en listado: match expediente + juzgado"
                        )
                        validadas.append(c)
                        continue
                    # Sin actor y sin juzgado corroborable → REVISION manual
                    c.motivo = (
                        "Actor no declarado en listado y sin juzgado corroborable. "
                        "Coincide únicamente expediente — requiere revisión humana."
                    )
                    revision.append(c)
                    continue

                if juzgado_match:
                    c.ruta_validacion = "B_juzgado"
                    c.motivo = (
                        "Actor del listado no aparece en el bloque, "
                        "pero el juzgado del listado coincide con la sección"
                    )
                    validadas.append(c)
                    continue

                # Filtro anti-homónimo: si ni actor ni cliente aparecen en
                # la HOJA COMPLETA *con proximidad* entre sus tokens,
                # descartamos silenciosamente (homónimo puro).
                # Proximidad evita matches espurios donde los tokens del
                # nombre están dispersos en pleitos distintos de la página.
                hoja_norm = normalizar_texto(hoja.texto)
                actor_en_hoja = (
                    reg.actor and not reg.actor_reservado
                    and alguna_parte_junta_en_texto(reg.actor, hoja_norm)
                )
                cliente_en_hoja = (
                    reg.cliente
                    and alguna_parte_junta_en_texto(reg.cliente, hoja_norm)
                )
                if not actor_en_hoja and not cliente_en_hoja:
                    # Homónimo: ni actor ni cliente aparecen en la hoja → descartar
                    continue

                # Aparece pero no en el bloque del expediente: revisión humana
                c.motivo = (
                    f"Expediente {exp}: actor/cliente del listado aparece en la hoja "
                    f"pero NO dentro de la ventana de contexto del expediente"
                )
                revision.append(c)

    return validadas, revision
