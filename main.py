# main.py
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import pymysql
import os
from datetime import datetime, timedelta, time
import random
from dotenv import load_dotenv
from typing import List

import pymysql


load_dotenv()

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_USER = os.getenv("DB_USER", "root")
DB_PASS = os.getenv("DB_PASS", "4815926")
DB_NAME = os.getenv("DB_NAME", "simulador")
DB_PORT = int(os.getenv("DB_PORT", "3306"))

TOTAL_ACTIVIDADES_DIARIAS = 3

# Rango horario para generar actividades (en horas, formato 24h)
# Actividades se generarán entre HORA_INICIO_ACTIVIDADES y HORA_FIN_ACTIVIDADES
HORA_INICIO_ACTIVIDADES = int(os.getenv("HORA_INICIO_ACTIVIDADES", "13"))   # 9 AM por defecto
HORA_FIN_ACTIVIDADES = int(os.getenv("HORA_FIN_ACTIVIDADES", "15"))         # 9 PM por defecto

# Separación mínima entre actividades (en minutos)
MIN_SEPARACION_ACTIVIDADES = int(os.getenv("MIN_SEPARACION_ACTIVIDADES", "10"))
# Weighted activities
ACTIVIDADES_PESOS = [
    ("Alimentar", 5),
    ("Cambiar pañal", 4),
    ("Dormir", 3),
    ("Bañar", 2),
    ("Curar", 1),
]

# Aplicación FastAPI
app = FastAPI(title="Simulador Bebé - Backend (FastAPI)")

# Configurar CORS: ORIGENES_FRONTEND puede ser una lista separada por comas o '*' por defecto
_origenes_env = os.getenv("ORIGENES_FRONTEND", "*")
if _origenes_env.strip() == "*":
    ORIGENES_PERMITIDOS = ["*"]
else:
    ORIGENES_PERMITIDOS = [o.strip() for o in _origenes_env.split(",") if o.strip()]

# Decide whether to allow credentials based on environment and origins.
_allow_credentials_env = os.getenv("ALLOW_CREDENTIALS", "true").lower()
_allow_credentials = True if _allow_credentials_env in ("1", "true", "yes") else False
# If origins is wildcard and credentials are requested, browsers will reject responses.
if ORIGENES_PERMITIDOS == ["*"] and _allow_credentials:
    print(
        "Warning: ALLOW_CREDENTIALS is true but ORIGENES_FRONTEND='*'. Disabling credentials for CORS to avoid browser rejection."
    )
    _allow_credentials = False

print(f"CORS configured. allow_origins={ORIGENES_PERMITIDOS}, allow_credentials={_allow_credentials}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ORIGENES_PERMITIDOS,
    allow_credentials=_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Simple DB helper (blocking). For production use a connection pool.
def obtener_conexion_db():
    
    """Ayudante simple de BD (bloqueante). En producción usar un pool de conexiones."""
    try:
        return pymysql.connect(
            host=DB_HOST,
            user=DB_USER,
            password=DB_PASS,
            database=DB_NAME,
            port=DB_PORT,
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=True,
            charset="utf8mb4",
        )
        print("Conexion a la base de datos exitosa.")
    except Exception as e:
        # Raise an HTTPException so FastAPI returns a clear 500 response instead of crashing.
        detail = (
            "Error connecting to the database.\n"
            "Possible causes: MySQL not running, wrong credentials, or authentication plugin (caching_sha2_password)\n"
            f"Original error: {e}"
        )
        raise HTTPException(status_code=500, detail=detail)

# --- Utilities ---
def seleccionar_actividad_ponderada() -> str:
    total = sum(p for _, p in ACTIVIDADES_PESOS)
    r = random.uniform(0, total)
    for actividad, peso in ACTIVIDADES_PESOS:
        r -= peso
        if r <= 0:
            return actividad
    return ACTIVIDADES_PESOS[-1][0]

def obtener_inicio_dia(dt: datetime) -> datetime:
    """
    Devuelve el inicio de ventana a HORA_INICIO_ACTIVIDADES que contiene la fecha/hora dada.
    Si dt >= hoy@HORA_INICIO -> devuelve hoy@HORA_INICIO, si no -> devuelve ayer@HORA_INICIO.
    """
    today_window = datetime(dt.year, dt.month, dt.day, HORA_INICIO_ACTIVIDADES, 0, 0)
    if dt >= today_window:
        return today_window
    return today_window - timedelta(days=1)

def generar_offsets_minutos(n: int, min_sep: int = 15, start_min: int = 0, end_min: int = 24*60-1) -> List[int]:
    """
    Genera n offsets en minutos entre start_min..end_min inclusive,
    intenta distribuirlos uniformemente y aplica separación mínima (min_sep).
    Garantiza que todos los offsets están dentro del rango [start_min, end_min].
    """
    if n <= 0:
        return []
    
    # Verificar si hay espacio suficiente para n actividades con separación mínima
    min_space_needed = start_min + (n - 1) * min_sep
    if min_space_needed > end_min:
        # Si no hay espacio, distribuir lo mejor posible
        offsets = []
        for i in range(n):
            offset = start_min + i * ((end_min - start_min) // n)
            offsets.append(offset)
        return offsets
    
    total_minutes = end_min - start_min + 1
    # Distribución uniforme en segmentos
    seg = max(1, total_minutes // n)
    offsets = []
    
    for i in range(n):
        seg_start = start_min + i * seg
        seg_end = min(start_min + (i + 1) * seg - 1, end_min)
        
        if seg_start > seg_end:
            pick = min(start_min + i * min_sep, end_min)
        else:
            pick = seg_start + random.randint(0, max(0, seg_end - seg_start))
        offsets.append(pick)
    
    offsets.sort()
    
    # Forzar separación mínima y mantener dentro del rango
    for i in range(1, len(offsets)):
        if offsets[i] - offsets[i-1] < min_sep:
            offsets[i] = offsets[i-1] + min_sep
            # Si se sobrepasa el límite, ajustar hacia atrás
            if offsets[i] > end_min:
                offsets[i] = end_min
    
    # Validar que todos los offsets estén dentro del rango
    for i in range(len(offsets)):
        offsets[i] = max(start_min, min(end_min, offsets[i]))
    
    offsets.sort()
    
    return offsets

# --- Funciones principales ---
def actividades_existen_para_dia(curp: str) -> bool:
    ahora = datetime.now()
    inicio_dia = obtener_inicio_dia(ahora)
    fin_ventana = inicio_dia + timedelta(days=1)
    sql = "SELECT COUNT(*) AS cnt FROM actividades WHERE curp = %s AND fecha_inicial >= %s AND fecha_inicial < %s"
    conn = obtener_conexion_db()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (curp, inicio_dia, fin_ventana))
            row = cur.fetchone()
            return row["cnt"] > 0
    finally:
        conn.close()

def generar_actividades_diarias_para_usuario(curp: str) -> int:
    """
    Genera `TOTAL_ACTIVIDADES_DIARIAS` para el usuario dentro de la ventana HORA_INICIO->HORA_INICIO del día siguiente,
    con actividades ponderadas aleatorias y tiempos (offsets) aleatorios con separación >= MIN_SEPARACION_ACTIVIDADES.
    Solo genera actividades dentro del rango HORA_INICIO_ACTIVIDADES a HORA_FIN_ACTIVIDADES.
    Devuelve el número de actividades insertadas.
    """
    ahora = datetime.now()
    inicio_dia = obtener_inicio_dia(ahora)
    fin_ventana = inicio_dia + timedelta(days=1)

    # Calcular rango de minutos dentro del día (desde medianoche de ese día)
    # Si inicio_dia es 12:00 PM y fin es 1:00 PM, queremos offsets de 0 a 59 minutos dentro de esa hora
    start_min = 0  # 0 minutos desde HORA_INICIO_ACTIVIDADES
    end_min = (HORA_FIN_ACTIVIDADES - HORA_INICIO_ACTIVIDADES) * 60 - 1  # diferencia en minutos
    
    # Colocamos offsets relativos al inicio de ventana dentro del rango permitido
    offsets = generar_offsets_minutos(TOTAL_ACTIVIDADES_DIARIAS, min_sep=MIN_SEPARACION_ACTIVIDADES, start_min=start_min, end_min=end_min)

    inserts = []
    for off in offsets:
        fecha_inicial = inicio_dia + timedelta(minutes=int(off))
        fecha_limite = fecha_inicial + timedelta(minutes=10)  # duración 10 minutos
        actividad = seleccionar_actividad_ponderada()
        inserts.append((actividad, fecha_inicial, fecha_limite, 0, curp))

    # Inserción en bloque
    sql = "INSERT INTO actividades (actividad, fecha_inicial, fecha_limite, completada, curp) VALUES (%s, %s, %s, %s, %s)"
    conn = obtener_conexion_db()
    try:
        with conn.cursor() as cur:
            cur.executemany(sql, inserts)
        return len(inserts)
    finally:
        conn.close()

def actualizar_estado_bebe_antes_login(curp: str):
    """
    Revisa la ventana previa (prev_inicio .. inicio_dia) y pone `bebe_vivo = 0` si
    el porcentaje de actividades completadas < 60%. Además, si el día pasado no tiene
    actividades pero existen actividades en días anteriores (antier o antes), se
    interpreta abandono y se pone `bebe_vivo = 0`.
    """
    conn = obtener_conexion_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM usuarios WHERE curp = %s LIMIT 1", (curp,))
            user = cur.fetchone()
            if not user:
                raise HTTPException(status_code=401, detail="CURP no encontrada")
            ahora = datetime.now()
            inicio_dia = obtener_inicio_dia(ahora)
            prev_inicio = inicio_dia - timedelta(days=1)
            prev_fin = inicio_dia

            sql = "SELECT completada FROM actividades WHERE curp = %s AND fecha_inicial >= %s AND fecha_inicial < %s"
            cur.execute(sql, (curp, prev_inicio, prev_fin))
            rows = cur.fetchall()
            total = len(rows)

            # Caso A: si hay actividades el día anterior, calcular porcentaje
            if total > 0:
                completadas = sum(1 for r in rows if r["completada"])
                porcentaje = (completadas / total) * 100
                if porcentaje < 60:
                    cur.execute("UPDATE usuarios SET bebe_vivo = 0 WHERE curp = %s", (curp,))
                # else: mantener el estado actual
            else:
                # Caso B: no hay actividades registradas el día anterior.
                # Si existen actividades anteriores a ese día (antier o antes),
                # interpretamos que el usuario dejó de conectarse => marcar bebe_vivo = 0.
                cur.execute(
                    "SELECT COUNT(*) AS cnt FROM actividades WHERE curp = %s AND fecha_inicial < %s",
                    (curp, prev_inicio),
                )
                older = cur.fetchone()
                older_cnt = older["cnt"] if older and "cnt" in older else 0
                if older_cnt > 0:
                    cur.execute("UPDATE usuarios SET bebe_vivo = 0 WHERE curp = %s", (curp,))
    finally:
        conn.close()

# --- Pydantic models ---
class RegistroEntrada(BaseModel):
    curp: str
    nombre: str
    edad: int
    bebe_vivo: bool = True

class LoginEntrada(BaseModel):
    curp: str

# --- Endpoints ---
@app.get("/ping")
def ping():
    """Simple health/CORS test endpoint."""
    return {"status": "ok"}

@app.post("/registro")
def registro(payload: RegistroEntrada):
    sql = "INSERT INTO usuarios (curp, nombre, edad, bebe_vivo) VALUES (%s, %s, %s, %s)"
    conn = obtener_conexion_db()
    try:
        with conn.cursor() as cur:
            try:
                cur.execute(sql, (payload.curp, payload.nombre, payload.edad, int(payload.bebe_vivo)))
            except pymysql.err.IntegrityError as e:
                raise HTTPException(status_code=400, detail="CURP ya existe o datos inválidos")
        return {"msg": "Usuario registrado correctamente"}
    finally:
        conn.close()

@app.post("/login")
def login(payload: LoginEntrada):
    if not payload.curp:
        raise HTTPException(status_code=400, detail="CURP requerida")

    # 1) Actualizar estado del bebé basado en la ventana previa ANTES de la lógica
    actualizar_estado_bebe_antes_login(payload.curp)

    # 2) Obtener usuario
    conn = obtener_conexion_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM usuarios WHERE curp = %s LIMIT 1", (payload.curp,))
            user = cur.fetchone()
            if not user:
                raise HTTPException(status_code=401, detail="CURP no encontrada")

        # 3) Si el usuario no tiene actividades para la ventana actual 9:00->9:00, generarlas
        existe = actividades_existen_para_dia(payload.curp)
        if not existe:
            # refresh user record (to get updated bebe_vivo)
            conn2 = obtener_conexion_db()
            bebe_vivo = False
            try:
                with conn2.cursor() as cur2:
                    cur2.execute("SELECT bebe_vivo FROM usuarios WHERE curp = %s LIMIT 1", (payload.curp,))
                    u2 = cur2.fetchone()
                    bebe_vivo = bool(u2["bebe_vivo"]) if u2 else False
            finally:
                conn2.close()

            if bebe_vivo:
                n = generar_actividades_diarias_para_usuario(payload.curp)
                print(f"Generadas {n} actividades para {payload.curp}")
            else:
                print(f"No se generan actividades para {payload.curp} porque bebe_vivo=false")

        # Devolver datos del usuario (actualizados)
        conn3 = obtener_conexion_db()
        try:
            with conn3.cursor() as cur3:
                cur3.execute("SELECT * FROM usuarios WHERE curp = %s LIMIT 1", (payload.curp,))
                ufinal = cur3.fetchone()
        finally:
            conn3.close()

        return {"msg": "Login exitoso", "usuario": ufinal}
    finally:
        conn.close()

# Optional: endpoint to list today's activities (for frontend)
@app.get("/actividades/dia/{curp}")
def actividades_dia(curp: str):
    ahora = datetime.now()
    inicio_dia = obtener_inicio_dia(ahora)
    fin_ventana = inicio_dia + timedelta(days=1)
    sql = "SELECT id, actividad, fecha_inicial, fecha_limite, completada FROM actividades WHERE curp = %s AND fecha_inicial >= %s AND fecha_inicial < %s ORDER BY fecha_inicial"
    conn = obtener_conexion_db()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (curp, inicio_dia, fin_ventana))
            rows = cur.fetchall()
            return {"actividades": rows}
    finally:
        conn.close()


@app.get("/actividades/reporte/{curp}")
def actividades_todas(curp: str):
    """Devuelve todas las actividades registradas para la `curp` indicada.
    Ordenadas por `fecha_inicial` descendente (más recientes primero).
    """
    sql = "SELECT id, actividad, fecha_inicial, fecha_limite, completada, curp FROM actividades WHERE curp = %s ORDER BY fecha_inicial DESC"
    conn = obtener_conexion_db()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (curp,))
            rows = cur.fetchall()
            return {"actividades": rows}
    finally:
        conn.close()

# Optional: mark activity completed
class CompletarEntrada(BaseModel):
    id: int


@app.post("/actividades/completar")
def completar_actividad(payload: CompletarEntrada):
    sql = "UPDATE actividades SET completada = 1 WHERE id = %s"
    conn = obtener_conexion_db()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (payload.id,))
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="Actividad no encontrada o ya completada")
        return {"msg": "Actividad marcada como completada"}
    finally:
        conn.close()

# import pymysql

# try:
#     conn = pymysql.connect(
#         host="localhost",
#         user="root",
#         password="4815926",
#         database="simulador",
#         port=3306
#     )
#     print("CONEXIÓN OK ✔️")
#     conn.close()
# except Exception as e:
#     print("ERROR ❌:", e)
