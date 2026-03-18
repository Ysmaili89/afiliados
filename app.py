from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, abort
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from datetime import datetime, timedelta
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
from markupsafe import escape
from collections import defaultdict
from time import time
import logging
import random
import os
import re

# ===== API DE AMAZON (COMENTADA - PARA USO FUTURO) =====
# try:
#     from amazon_paapi import AmazonApi
#     AMAZON_API_DISPONIBLE = True
# except ImportError:
#     AMAZON_API_DISPONIBLE = False
#     print("ℹ️ API de Amazon no disponible - modo manual activado")

# ===== CONFIGURACIÓN DE LOGGING MEJORADA =====
# Configuración básica
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()  # Solo consola inicialmente
    ]
)

# Configurar manejador de archivo para WARNING y superior
file_handler = logging.FileHandler('app.log')
file_handler.setLevel(logging.WARNING)  # Solo warnings y errors en archivo
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))

# Obtener logger raíz y configurarlo
logger = logging.getLogger()
logger.handlers = []  # Limpiar handlers existentes
logger.addHandler(file_handler)
logger.addHandler(logging.StreamHandler())
logger.setLevel(logging.INFO)

logger = logging.getLogger(__name__)

app = Flask(__name__)

# Configuración de seguridad
IS_PRODUCTION = os.environ.get('PRODUCTION', 'False').lower() == 'true'

# Configuración desde variables de entorno (OBLIGATORIO EN PRODUCCIÓN)
app.secret_key = os.environ.get('SECRET_KEY')
if not app.secret_key:
    if IS_PRODUCTION:
        raise ValueError("""
        ⚠️ ERROR CRÍTICO: Variable de entorno SECRET_KEY no configurada.
        En producción, debes establecer una SECRET_KEY segura.
        Ejemplo: export SECRET_KEY='tu-clave-secreta-muy-segura-aqui'
        """)
    else:
        # Solo para desarrollo, usar una clave aleatoria
        app.secret_key = os.urandom(24).hex()
        print("⚠️ ADVERTENCIA: Usando SECRET_KEY aleatoria en desarrollo")

app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)

# Configuración de base de datos (USAR VARIABLE DE ENTORNO EN PRODUCCIÓN)
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get(
    'DATABASE_URL',  # Variable común en hostings (Render, PythonAnywhere, etc.)
    'sqlite:///filtro_amazon.db'  # Fallback para desarrollo local
)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

app.config.update(
    SESSION_COOKIE_SECURE=IS_PRODUCTION,  # Solo HTTPS en producción
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    MAX_CONTENT_LENGTH=16 * 1024 * 1024
)

# ===== VARIABLES DE API PARA USO FUTURO (COMENTADAS) =====
# AMAZON_ACCESS_KEY = os.environ.get('AMAZON_ACCESS_KEY', '')
# AMAZON_SECRET_KEY = os.environ.get('AMAZON_SECRET_KEY', '')
# AMAZON_ASSOCIATE_TAG = os.environ.get('AMAZON_ASSOCIATE_TAG', '')
# AMAZON_COUNTRY = os.environ.get('AMAZON_COUNTRY', 'es')

# Inicializar extensiones
db = SQLAlchemy(app)
csrf = CSRFProtect(app)

# Control de intentos de login
intentos_login = defaultdict(list)

# ========== MODELOS DE BASE DE DATOS ==========
class Usuario(db.Model):
    __tablename__ = 'usuarios'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    nombre = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    rol = db.Column(db.String(20), default='viewer')
    fecha_registro = db.Column(db.String(20), default=lambda: datetime.now().strftime('%d/%m/%Y'))
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class Producto(db.Model):
    __tablename__ = 'productos'
    id = db.Column(db.Integer, primary_key=True)
    titulo = db.Column(db.String(200), nullable=False)
    descripcion = db.Column(db.Text, nullable=False)
    imagen = db.Column(db.String(500), nullable=False)
    url = db.Column(db.String(500), nullable=False)
    categoria = db.Column(db.String(50), nullable=False)
    destacado = db.Column(db.Boolean, default=False)
    clics = db.Column(db.Integer, default=0)
    fecha_creacion = db.Column(db.String(20), default=lambda: datetime.now().strftime('%Y-%m-%d'))

class Categoria(db.Model):
    __tablename__ = 'categorias'
    id = db.Column(db.Integer, primary_key=True)
    categoria_id = db.Column(db.String(50), unique=True, nullable=False)
    nombre = db.Column(db.String(100), nullable=False)
    icono = db.Column(db.String(50), default='tag')

class Configuracion(db.Model):
    __tablename__ = 'configuracion'
    id = db.Column(db.Integer, primary_key=True)
    clave = db.Column(db.String(50), unique=True, nullable=False)
    valor = db.Column(db.String(500), nullable=False)

class Visita(db.Model):
    __tablename__ = 'visitas'
    id = db.Column(db.Integer, primary_key=True)
    pagina = db.Column(db.String(200), nullable=False)
    fecha = db.Column(db.String(20), default=lambda: datetime.now().strftime('%Y-%m-%d'))
    ip = db.Column(db.String(50))
    user_agent = db.Column(db.String(200))

# ========== CONFIGURACIÓN INICIAL ==========
def init_db():
    """Inicializa la base de datos con datos por defecto"""
    db.create_all()
    
    # Crear usuario admin por defecto si no existe
    if not Usuario.query.filter_by(username='admin').first():
        admin = Usuario(
            username='admin',
            nombre='Administrador',
            email='admin@miafiliado.com',
            rol='admin'
        )
        admin.set_password('admin123')
        db.session.add(admin)
    
    # Crear usuario editor por defecto
    if not Usuario.query.filter_by(username='editor').first():
        editor = Usuario(
            username='editor',
            nombre='Editor',
            email='editor@miafiliado.com',
            rol='editor'
        )
        editor.set_password('editor123')
        db.session.add(editor)
    
    # Categorías por defecto
    categorias_default = [
        ('electronica', 'Electrónica', 'laptop'),
        ('hogar', 'Hogar', 'home'),
        ('libros', 'Libros', 'book'),
        ('moda', 'Moda', 'tshirt'),
        ('deportes', 'Deportes', 'futbol'),
        ('juguetes', 'Juguetes', 'gamepad'),
        ('belleza', 'Belleza', 'spa'),
        ('alimentacion', 'Alimentación', 'utensils'),
        ('mascotas', 'Mascotas', 'paw'),
        ('bebe', 'Bebé', 'baby'),
        ('herramientas', 'Herramientas', 'tools'),
        ('jardin', 'Jardín', 'seedling'),
        ('automocion', 'Automoción', 'car'),
        ('salud', 'Salud', 'heartbeat')
    ]
    
    for cat_id, nombre, icono in categorias_default:
        if not Categoria.query.filter_by(categoria_id=cat_id).first():
            cat = Categoria(categoria_id=cat_id, nombre=nombre, icono=icono)
            db.session.add(cat)
    
    # Productos de ejemplo
    if Producto.query.count() == 0:
        productos_ejemplo = [
            Producto(
                titulo='Apple AirPods Pro (2ª generación)',
                descripcion='Auriculares inalámbricos con cancelación de ruido, chip H2, audio espacial personalizado',
                imagen='https://m.media-amazon.com/images/I/61SUj2aKoEL._AC_SL1500_.jpg',
                url='https://www.amazon.es/dp/B0BDK62PDX/',
                categoria='electronica',
                destacado=True,
                clics=150,
                fecha_creacion='2024-01-15'
            ),
            Producto(
                titulo='Kindle Paperwhite (11ª generación)',
                descripcion='Pantalla de 6.8 pulgadas, luz cálida ajustable, resistente al agua IPX8',
                imagen='https://m.media-amazon.com/images/I/61L6+U6K6OL._AC_SL1500_.jpg',
                url='https://www.amazon.es/dp/B08N2XW7Z3/',
                categoria='libros',
                destacado=True,
                clics=89,
                fecha_creacion='2024-01-20'
            ),
            Producto(
                titulo='Echo Dot (5ª generación)',
                descripcion='Altavoz inteligente con Alexa, sonido mejorado y diseño compacto',
                imagen='https://m.media-amazon.com/images/I/61+x3oG7SLL._AC_SL1500_.jpg',
                url='https://www.amazon.es/dp/B09B8V1LZ3/',
                categoria='electronica',
                destacado=False,
                clics=45,
                fecha_creacion='2024-02-01'
            )
        ]
        
        for prod in productos_ejemplo:
            db.session.add(prod)
    
    # Configuración por defecto
    config_defaults = {
        'nombre': 'Mi Filtro Amazon',
        'dominio': 'miafiliado.com',
        'email_contacto': 'info@miafiliado.com',
        'telefono': '+34 123 456 789',
        'direccion': 'Madrid, España',
        'amazon_tag': 'miafiliado-21',
        'color_principal': '#ff9900',
        'meta_description': 'Encuentra los mejores productos de Amazon seleccionados especialmente para ti.',
        'meta_keywords': 'amazon, productos, filtro, ofertas, recomendaciones',
        'google_analytics': ''
    }
    
    for clave, valor in config_defaults.items():
        if not Configuracion.query.filter_by(clave=clave).first():
            conf = Configuracion(clave=clave, valor=valor)
            db.session.add(conf)
    
    try:
        db.session.commit()
        logger.info("Base de datos inicializada correctamente")
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error inicializando base de datos: {e}")

# ========== FUNCIONES DE AYUDA ==========
def get_config():
    """Obtiene la configuración como diccionario"""
    config = {}
    for item in Configuracion.query.all():
        config[item.clave] = item.valor
    return config

def get_categorias():
    """Obtiene las categorías como lista de diccionarios"""
    categorias = Categoria.query.all()
    return [{'id': c.categoria_id, 'nombre': c.nombre, 'icono': c.icono} for c in categorias]

def get_categorias_lista():
    """Obtiene solo los IDs de categorías"""
    return [c.categoria_id for c in Categoria.query.all()]

def actualizar_estadisticas(pagina):
    """Registra una visita en la base de datos"""
    try:
        visita = Visita(
            pagina=pagina,
            ip=request.remote_addr,
            user_agent=request.user_agent.string[:200] if request.user_agent else ''
        )
        db.session.add(visita)
        db.session.commit()
    except Exception as e:
        logger.error(f"Error registrando visita: {e}")

def get_estadisticas():
    """Obtiene estadísticas de visitas"""
    hoy = datetime.now().strftime('%Y-%m-%d')
    semana_inicio = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
    mes_inicio = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
    
    total = Visita.query.count()
    hoy_count = Visita.query.filter_by(fecha=hoy).count()
    semana_count = Visita.query.filter(Visita.fecha >= semana_inicio).count()
    mes_count = Visita.query.filter(Visita.fecha >= mes_inicio).count()
    
    # Páginas más visitadas
    paginas = {}
    visitas_por_pagina = db.session.query(
        Visita.pagina, db.func.count(Visita.id)
    ).group_by(Visita.pagina).order_by(db.func.count(Visita.id).desc()).limit(10).all()
    
    for pagina, count in visitas_por_pagina:
        paginas[pagina] = count
    
    return {
        'total': total,
        'hoy': hoy_count,
        'semana': semana_count,
        'mes': mes_count,
        'paginas': paginas,
        'fecha_actualizacion': hoy
    }

def get_visitas_por_dia():
    """Obtiene visitas de los últimos 7 días para el gráfico"""
    hoy = datetime.now()
    dias_semana = ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo']
    resultados = []
    max_visitas = 0
    
    for i in range(7):
        fecha_obj = hoy - timedelta(days=6-i)
        fecha_str = fecha_obj.strftime('%Y-%m-%d')
        dia_nombre = dias_semana[fecha_obj.weekday()]
        visitas = Visita.query.filter_by(fecha=fecha_str).count()
        
        if visitas > max_visitas:
            max_visitas = visitas
        
        resultados.append({
            'fecha': fecha_str,
            'dia_corto': dia_nombre[:3],
            'dia_completo': dia_nombre,
            'visitas': visitas,
            'altura': 0
        })
    
    for item in resultados:
        if max_visitas > 0:
            item['altura'] = (item['visitas'] / max_visitas * 100)
        else:
            item['altura'] = 0
    
    return resultados

# ========== DECORADORES ==========
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'usuario' not in session:
            flash('Por favor inicia sesión para acceder al panel', 'warning')
            return redirect(url_for('panel_login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get('rol') != 'admin':
            flash('No tienes permisos para acceder a esta sección', 'error')
            return redirect(url_for('panel_dashboard'))
        return f(*args, **kwargs)
    return decorated_function

# ========== CONTEXTO GLOBAL ==========
@app.context_processor
def inject_now():
    """Inyecta datetime.now() y timedelta en todas las plantillas"""
    from datetime import timedelta
    return {
        'now': datetime.now(),
        'timedelta': timedelta
    }

@app.context_processor
def inject_visitas():
    return {'visitas': get_estadisticas()}

@app.context_processor
def inject_config():
    return {'config': get_config()}

# ========== RUTAS DEL PANEL ==========
@app.route('/panel/login', methods=['GET', 'POST'])
def panel_login():
    ip = request.remote_addr
    ahora = time()
    
    intentos_login[ip] = [t for t in intentos_login[ip] if ahora - t < 900]
    
    if len(intentos_login[ip]) >= 5:
        logger.warning(f"Demasiados intentos de login desde IP: {ip}")
        flash('Demasiados intentos. Espera 15 minutos.', 'error')
        return render_template('panel/login.html')
    
    if request.method == 'POST':
        intentos_login[ip].append(ahora)
        
        username = escape(request.form.get('username', ''))
        password = request.form.get('password', '')
        
        usuario = Usuario.query.filter_by(username=username).first()
        
        if usuario and usuario.check_password(password):
            session['usuario'] = usuario.username
            session['nombre'] = usuario.nombre
            session['rol'] = usuario.rol
            session.permanent = True
            logger.info(f"Login exitoso: {username} desde IP {ip}")
            flash(f'¡Bienvenido {usuario.nombre}!', 'success')
            return redirect(url_for('panel_dashboard'))
        else:
            logger.warning(f"Intento de login fallido: {username} desde IP {ip}")
            flash('Usuario o contraseña incorrectos', 'error')
    
    return render_template('panel/login.html')

@app.route('/panel/logout')
def panel_logout():
    usuario = session.get('usuario', 'desconocido')
    logger.info(f"Logout: {usuario}")
    session.clear()
    flash('Has cerrado sesión correctamente', 'info')
    return redirect(url_for('index'))

@app.route('/panel')
@login_required
def panel_dashboard():
    actualizar_estadisticas('panel_dashboard')
    
    total_productos = Producto.query.count()
    total_clics = db.session.query(db.func.sum(Producto.clics)).scalar() or 0
    total_usuarios = Usuario.query.count()
    categorias = get_categorias()
    
    top_productos = Producto.query.order_by(Producto.clics.desc()).limit(5).all()
    
    return render_template('panel/dashboard.html',
                         total_productos=total_productos,
                         total_clics=total_clics,
                         total_usuarios=total_usuarios,
                         top_productos=top_productos,
                         categorias=categorias,
                         usuario=session.get('nombre'))

@app.route('/panel/productos')
@login_required
def panel_productos():
    actualizar_estadisticas('panel_productos')
    
    busqueda = request.args.get('buscar', '')
    busqueda_limpia = escape(busqueda).lower()
    
    query = Producto.query
    if busqueda_limpia:
        query = query.filter(Producto.titulo.ilike(f'%{busqueda_limpia}%'))
    
    productos = query.order_by(Producto.id.desc()).all()
    total_clics = sum(p.clics for p in productos)
    categorias = get_categorias()
    categorias_lista = get_categorias_lista()
    
    return render_template('panel/productos.html',
                         productos=productos,
                         total_clics=total_clics,
                         categorias=categorias,
                         categorias_lista=categorias_lista,
                         usuario=session.get('nombre'))

@app.route('/panel/productos/nuevo', methods=['GET', 'POST'])
@login_required
def panel_producto_nuevo():
    actualizar_estadisticas('panel_producto_nuevo')
    
    if request.method == 'POST':
        titulo = escape(request.form.get('titulo', ''))
        descripcion = escape(request.form.get('descripcion', ''))
        imagen = escape(request.form.get('imagen', ''))
        url = escape(request.form.get('url', ''))
        categoria = escape(request.form.get('categoria', ''))
        destacado = True if request.form.get('destacado') == 'true' else False

        if not imagen.startswith(('http://', 'https://')):
            flash('La URL de la imagen debe comenzar con http:// o https://', 'error')
            return redirect(url_for('panel_producto_nuevo'))

        nuevo_producto = Producto(
            titulo=titulo,
            descripcion=descripcion,
            imagen=imagen,
            url=url,
            categoria=categoria,
            destacado=destacado,
            clics=0
        )
        
        try:
            db.session.add(nuevo_producto)
            db.session.commit()
            logger.info(f"Producto creado: {titulo}")
            flash(f'Producto "{titulo}" creado correctamente', 'success')
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error creando producto: {e}")
            flash('Error al crear el producto', 'error')
        
        return redirect(url_for('panel_productos'))
    
    return render_template('panel/producto_form.html',
                         categorias=get_categorias(),
                         categorias_lista=get_categorias_lista(),
                         producto=None,
                         usuario=session.get('nombre'))

@app.route('/panel/productos/editar/<int:producto_id>', methods=['GET', 'POST'])
@login_required
def panel_producto_editar(producto_id):
    actualizar_estadisticas('panel_producto_editar')
    
    producto = Producto.query.get_or_404(producto_id)
    
    if request.method == 'POST':
        producto.titulo = escape(request.form.get('titulo', ''))
        producto.descripcion = escape(request.form.get('descripcion', ''))
        producto.imagen = escape(request.form.get('imagen', ''))
        producto.url = escape(request.form.get('url', ''))
        producto.categoria = escape(request.form.get('categoria', ''))
        producto.destacado = True if request.form.get('destacado') == 'true' else False
        
        try:
            producto.clics = int(request.form.get('clics', 0))
        except ValueError:
            producto.clics = 0
            flash('El valor de clics no es válido, se usará 0.', 'warning')
        
        try:
            db.session.commit()
            logger.info(f"Producto actualizado: {producto.titulo}")
            flash('Producto actualizado correctamente', 'success')
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error actualizando producto: {e}")
            flash('Error al actualizar el producto', 'error')
        
        return redirect(url_for('panel_productos'))
    
    return render_template('panel/producto_form.html',
                         producto=producto,
                         categorias=get_categorias(),
                         categorias_lista=get_categorias_lista(),
                         usuario=session.get('nombre'))

@app.route('/panel/productos/eliminar/<int:producto_id>', methods=['POST'])
@login_required
def panel_producto_eliminar(producto_id):
    actualizar_estadisticas('panel_producto_eliminar')
    
    producto = Producto.query.get_or_404(producto_id)
    titulo = producto.titulo
    
    try:
        db.session.delete(producto)
        db.session.commit()
        logger.info(f"Producto eliminado: {titulo}")
        flash('Producto eliminado correctamente', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error eliminando producto: {e}")
        flash('Error al eliminar el producto', 'error')
    
    return redirect(url_for('panel_productos'))

@app.route('/panel/productos/duplicar/<int:producto_id>', methods=['POST'])
@login_required
def panel_producto_duplicar(producto_id):
    actualizar_estadisticas('panel_producto_duplicar')
    
    original = Producto.query.get_or_404(producto_id)
    
    nuevo = Producto(
        titulo=original.titulo + ' (copia)',
        descripcion=original.descripcion,
        imagen=original.imagen,
        url=original.url,
        categoria=original.categoria,
        destacado=original.destacado,
        clics=0
    )
    
    try:
        db.session.add(nuevo)
        db.session.commit()
        logger.info(f"Producto duplicado: {original.titulo} -> {nuevo.titulo}")
        flash('Producto duplicado correctamente', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error duplicando producto: {e}")
        flash('Error al duplicar el producto', 'error')
    
    return redirect(url_for('panel_productos'))

@app.route('/panel/categorias')
@login_required
def panel_categorias():
    actualizar_estadisticas('panel_categorias')
    
    categorias = get_categorias()
    
    for cat in categorias:
        cat['productos_count'] = Producto.query.filter_by(categoria=cat['id']).count()
    
    categorias_con_productos = len([c for c in categorias if c['productos_count'] > 0])
    categorias_lista = get_categorias_lista()
    
    return render_template('panel/categorias.html',
                         categorias=categorias,
                         categorias_lista=categorias_lista,
                         categorias_con_productos=categorias_con_productos,
                         productos=Producto.query.all(),
                         usuario=session.get('nombre'))

@app.route('/panel/categorias/nueva', methods=['POST'])
@login_required
@admin_required
def panel_categoria_nueva():
    actualizar_estadisticas('panel_categoria_nueva')
    
    nombre = escape(request.form.get('nombre', ''))
    slug_input = request.form.get('slug', '')
    slug = escape(slug_input.lower().replace(' ', '-')) if slug_input else escape(nombre.lower().replace(' ', '-'))
    
    if not re.match(r'^[a-z0-9-]+$', slug):
        flash('El slug solo puede contener letras minúsculas, números y guiones', 'error')
        return redirect(url_for('panel_categorias'))
    
    if Categoria.query.filter_by(categoria_id=slug).first():
        flash(f'La categoría "{nombre}" ya existe', 'error')
    else:
        nueva = Categoria(categoria_id=slug, nombre=nombre)
        try:
            db.session.add(nueva)
            db.session.commit()
            logger.info(f"Categoría creada: {nombre}")
            flash(f'Categoría "{nombre}" creada correctamente', 'success')
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error creando categoría: {e}")
            flash('Error al crear la categoría', 'error')
    
    return redirect(url_for('panel_categorias'))

@app.route('/panel/categorias/eliminar/<categoria_id>', methods=['POST'])
@login_required
@admin_required
def panel_categoria_eliminar(categoria_id):
    actualizar_estadisticas('panel_categoria_eliminar')
    
    categoria_id_limpio = escape(categoria_id)
    categoria = Categoria.query.filter_by(categoria_id=categoria_id_limpio).first()
    
    if not categoria:
        flash('Categoría no encontrada', 'error')
        return redirect(url_for('panel_categorias'))
    
    productos_en_categoria = Producto.query.filter_by(categoria=categoria_id_limpio).count()
    
    if productos_en_categoria > 0:
        flash(f'No se puede eliminar: hay {productos_en_categoria} productos en esta categoría', 'error')
        return redirect(url_for('panel_categorias'))
    
    try:
        db.session.delete(categoria)
        db.session.commit()
        logger.info(f"Categoría eliminada: {categoria.nombre}")
        flash(f'Categoría "{categoria.nombre}" eliminada correctamente', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error eliminando categoría: {e}")
        flash('Error al eliminar la categoría', 'error')
    
    return redirect(url_for('panel_categorias'))

# ===== RUTAS DE PERFIL Y USUARIOS (CORREGIDAS Y COMPLETAS) =====

@app.route('/panel/perfil', methods=['GET', 'POST'])
@login_required
def panel_perfil():
    actualizar_estadisticas('panel_perfil')
    
    username = session.get('usuario')
    usuario = Usuario.query.filter_by(username=username).first_or_404()
    
    if request.method == 'POST':
        # Actualizar datos básicos del perfil
        usuario.nombre = escape(request.form.get('nombre', usuario.nombre))
        usuario.email = escape(request.form.get('email', usuario.email))
        
        # Validar que el email no esté en uso por otro usuario
        email_existente = Usuario.query.filter(
            Usuario.email == usuario.email,
            Usuario.username != username
        ).first()
        
        if email_existente:
            flash('El email ya está registrado por otro usuario', 'error')
            return redirect(url_for('panel_perfil'))
        
        # 🔐 CAMBIO DE CONTRASEÑA DESDE EL MISMO FORMULARIO
        password_actual = request.form.get('password_actual', '')
        password_nueva = request.form.get('password_nueva', '')
        password_confirmar = request.form.get('password_confirmar', '')
        
        # Si se proporcionó alguna contraseña, validar el cambio
        if password_actual or password_nueva or password_confirmar:
            # Verificar que todos los campos estén completos
            if not password_actual or not password_nueva or not password_confirmar:
                flash('Debes completar todos los campos de contraseña', 'error')
                return redirect(url_for('panel_perfil'))
            
            # Verificar contraseña actual
            if not usuario.check_password(password_actual):
                flash('La contraseña actual es incorrecta', 'error')
                return redirect(url_for('panel_perfil'))
            
            # Validar nueva contraseña
            if len(password_nueva) < 6:
                flash('La nueva contraseña debe tener al menos 6 caracteres', 'error')
                return redirect(url_for('panel_perfil'))
            
            if password_nueva != password_confirmar:
                flash('Las contraseñas no coinciden', 'error')
                return redirect(url_for('panel_perfil'))
            
            # Actualizar contraseña
            usuario.set_password(password_nueva)
            flash('Contraseña actualizada correctamente', 'success')
        
        try:
            db.session.commit()
            session['nombre'] = usuario.nombre
            logger.info(f"Perfil actualizado: {username}")
            if not (password_actual or password_nueva or password_confirmar):
                flash('Perfil actualizado correctamente', 'success')
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error actualizando perfil: {e}")
            flash('Error al actualizar el perfil', 'error')
        
        return redirect(url_for('panel_perfil'))
    
    return render_template('panel/perfil.html',
                         usuario_data=usuario,
                         usuarios=Usuario.query.all(),
                         total_usuarios=Usuario.query.count(),
                         categorias=get_categorias(),
                         categorias_lista=get_categorias_lista(),
                         usuario=session.get('nombre'))

@app.route('/panel/usuarios/nuevo', methods=['POST'])
@login_required
@admin_required
def panel_usuario_nuevo():
    actualizar_estadisticas('panel_usuario_nuevo')
    
    nuevo_username = escape(request.form.get('nuevo_username', ''))
    nuevo_nombre = escape(request.form.get('nuevo_nombre', ''))
    nuevo_email = escape(request.form.get('nuevo_email', ''))
    nuevo_rol = escape(request.form.get('nuevo_rol', 'viewer'))
    nuevo_password = request.form.get('nuevo_password', '')
    nuevo_confirm = request.form.get('nuevo_confirm', '')
    
    # Validaciones
    if Usuario.query.filter_by(username=nuevo_username).first():
        flash('El nombre de usuario ya existe', 'error')
        return redirect(url_for('panel_perfil') + '#usuarios')
    
    if Usuario.query.filter_by(email=nuevo_email).first():
        flash('El email ya está registrado', 'error')
        return redirect(url_for('panel_perfil') + '#usuarios')
    
    if len(nuevo_password) < 6:
        flash('La contraseña debe tener al menos 6 caracteres', 'error')
        return redirect(url_for('panel_perfil') + '#usuarios')
    
    if nuevo_password != nuevo_confirm:
        flash('Las contraseñas no coinciden', 'error')
        return redirect(url_for('panel_perfil') + '#usuarios')
    
    # Crear usuario
    usuario = Usuario(
        username=nuevo_username,
        nombre=nuevo_nombre,
        email=nuevo_email,
        rol=nuevo_rol
    )
    usuario.set_password(nuevo_password)
    
    try:
        db.session.add(usuario)
        db.session.commit()
        logger.info(f"Usuario creado: {nuevo_username}")
        flash(f'Usuario {nuevo_username} creado correctamente', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error creando usuario: {e}")
        flash('Error al crear el usuario', 'error')
    
    return redirect(url_for('panel_perfil') + '#usuarios')


@app.route('/panel/usuarios/editar', methods=['POST'])
@login_required
@admin_required
def panel_usuario_editar():
    """Editar un usuario existente (solo admin)"""
    actualizar_estadisticas('panel_usuario_editar')
    
    username = escape(request.form.get('edit_username', ''))
    nombre = escape(request.form.get('edit_nombre', ''))
    email = escape(request.form.get('edit_email', ''))
    rol = escape(request.form.get('edit_rol', 'viewer'))
    nueva_password = request.form.get('edit_password', '')
    confirm_password = request.form.get('edit_confirm', '')
    
    usuario = Usuario.query.filter_by(username=username).first()
    
    if not usuario:
        flash('Usuario no encontrado', 'error')
        return redirect(url_for('panel_perfil') + '#usuarios')
    
    # Validar email único (excepto para este usuario)
    email_existente = Usuario.query.filter(
        Usuario.email == email,
        Usuario.username != username
    ).first()
    
    if email_existente:
        flash('El email ya está registrado por otro usuario', 'error')
        return redirect(url_for('panel_perfil') + '#usuarios')
    
    # Actualizar datos básicos
    usuario.nombre = nombre
    usuario.email = email
    usuario.rol = rol
    
    # Cambiar contraseña si se proporcionó
    if nueva_password:
        if len(nueva_password) < 6:
            flash('La contraseña debe tener al menos 6 caracteres', 'error')
            return redirect(url_for('panel_perfil') + '#usuarios')
        
        if nueva_password != confirm_password:
            flash('Las contraseñas no coinciden', 'error')
            return redirect(url_for('panel_perfil') + '#usuarios')
        
        usuario.set_password(nueva_password)
        flash('Contraseña actualizada correctamente', 'success')
    
    try:
        db.session.commit()
        logger.info(f"Usuario actualizado por admin: {username}")
        flash(f'Usuario {username} actualizado correctamente', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error actualizando usuario: {e}")
        flash('Error al actualizar el usuario', 'error')
    
    return redirect(url_for('panel_perfil') + '#usuarios')


@app.route('/panel/usuarios/eliminar/<username>', methods=['POST'])
@login_required
@admin_required
def panel_usuario_eliminar(username):
    actualizar_estadisticas('panel_usuario_eliminar')
    
    username_limpio = escape(username)
    
    if username_limpio == session.get('usuario'):
        flash('No puedes eliminar tu propio usuario', 'error')
        return redirect(url_for('panel_perfil') + '#usuarios')
    
    usuario = Usuario.query.filter_by(username=username_limpio).first()
    
    if usuario:
        try:
            db.session.delete(usuario)
            db.session.commit()
            logger.info(f"Usuario eliminado: {username_limpio}")
            flash(f'Usuario {username_limpio} eliminado correctamente', 'success')
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error eliminando usuario: {e}")
            flash('Error al eliminar el usuario', 'error')
    else:
        flash('Usuario no encontrado', 'error')
    
    return redirect(url_for('panel_perfil') + '#usuarios')


@app.route('/panel/usuarios/datos/<username>', methods=['GET'])
@login_required
@admin_required
def panel_usuario_datos(username):
    """API para obtener datos de un usuario (para el modal de edición)"""
    username_limpio = escape(username)
    usuario = Usuario.query.filter_by(username=username_limpio).first()
    
    if usuario:
        return jsonify({
            'username': usuario.username,
            'nombre': usuario.nombre,
            'email': usuario.email,
            'rol': usuario.rol
        })
    
    return jsonify({'error': 'Usuario no encontrado'}), 404

# ===== RUTAS DE CONFIGURACIÓN =====

@app.route('/panel/configuracion', methods=['GET', 'POST'])
@login_required
@admin_required
def panel_configuracion():
    actualizar_estadisticas('panel_configuracion')
    
    if request.method == 'POST':
        config_items = [
            ('nombre_sitio', 'nombre'),
            ('dominio', 'dominio'),
            ('email_contacto', 'email_contacto'),
            ('telefono', 'telefono'),
            ('direccion', 'direccion')
        ]
        
        try:
            for form_field, config_key in config_items:
                valor = escape(request.form.get(form_field, ''))
                item = Configuracion.query.filter_by(clave=config_key).first()
                if item:
                    item.valor = valor
            
            db.session.commit()
            logger.info("Configuración actualizada")
            flash('Configuración guardada correctamente', 'success')
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error actualizando configuración: {e}")
            flash('Error al guardar la configuración', 'error')
        
        return redirect(url_for('panel_configuracion'))
    
    return render_template('panel/config.html',
                         categorias=get_categorias(),
                         categorias_lista=get_categorias_lista(),
                         usuario=session.get('nombre'))

@app.route('/panel/configuracion/afiliado', methods=['POST'])
@login_required
@admin_required
def panel_config_afiliado():
    amazon_tag = escape(request.form.get('amazon_tag', ''))
    amazon_pais = escape(request.form.get('amazon_pais', 'es'))
    
    try:
        tag_item = Configuracion.query.filter_by(clave='amazon_tag').first()
        if tag_item:
            tag_item.valor = amazon_tag
        
        pais_item = Configuracion.query.filter_by(clave='amazon_pais').first()
        if not pais_item:
            pais_item = Configuracion(clave='amazon_pais', valor=amazon_pais)
            db.session.add(pais_item)
        else:
            pais_item.valor = amazon_pais
        
        db.session.commit()
        logger.info("Configuración de afiliado actualizada")
        flash('Configuración de afiliado guardada', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error actualizando configuración de afiliado: {e}")
        flash('Error al guardar la configuración', 'error')
    
    return redirect(url_for('panel_configuracion'))

@app.route('/panel/configuracion/seo', methods=['POST'])
@login_required
@admin_required
def panel_config_seo():
    meta_description = escape(request.form.get('meta_description', ''))
    meta_keywords = escape(request.form.get('meta_keywords', ''))
    google_analytics = escape(request.form.get('google_analytics', ''))
    
    try:
        desc_item = Configuracion.query.filter_by(clave='meta_description').first()
        if desc_item:
            desc_item.valor = meta_description
        
        keywords_item = Configuracion.query.filter_by(clave='meta_keywords').first()
        if keywords_item:
            keywords_item.valor = meta_keywords
        
        ga_item = Configuracion.query.filter_by(clave='google_analytics').first()
        if ga_item:
            ga_item.valor = google_analytics
        
        db.session.commit()
        logger.info("Configuración SEO actualizada")
        flash('Configuración SEO guardada', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error actualizando configuración SEO: {e}")
        flash('Error al guardar la configuración', 'error')
    
    return redirect(url_for('panel_configuracion'))

@app.route('/panel/configuracion/apariencia', methods=['POST'])
@login_required
@admin_required
def panel_config_apariencia():
    color_principal = request.form.get('color_principal', '#ff9900')
    logo_url = escape(request.form.get('logo_url', ''))
    favicon_url = escape(request.form.get('favicon_url', ''))
    
    try:
        color_item = Configuracion.query.filter_by(clave='color_principal').first()
        if color_item:
            color_item.valor = color_principal
        
        logo_item = Configuracion.query.filter_by(clave='logo_url').first()
        if not logo_item:
            logo_item = Configuracion(clave='logo_url', valor=logo_url)
            db.session.add(logo_item)
        else:
            logo_item.valor = logo_url
        
        favicon_item = Configuracion.query.filter_by(clave='favicon_url').first()
        if not favicon_item:
            favicon_item = Configuracion(clave='favicon_url', valor=favicon_url)
            db.session.add(favicon_item)
        else:
            favicon_item.valor = favicon_url
        
        db.session.commit()
        logger.info("Configuración de apariencia actualizada")
        flash('Configuración de apariencia guardada', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error actualizando configuración de apariencia: {e}")
        flash('Error al guardar la configuración', 'error')
    
    return redirect(url_for('panel_configuracion'))

@app.route('/panel/estadisticas')
@login_required
def panel_estadisticas():
    actualizar_estadisticas('panel_estadisticas')
    
    hoy = datetime.now().strftime('%Y-%m-%d')
    ayer = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    
    visitas_hoy = Visita.query.filter_by(fecha=hoy).count()
    visitas_ayer = Visita.query.filter_by(fecha=ayer).count()
    
    # Obtener datos de visitas por día para el gráfico
    from collections import defaultdict
    visitas_por_dia_real = defaultdict(int)
    ultimos_7_dias = Visita.query.filter(
        Visita.fecha >= (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
    ).all()
    
    for v in ultimos_7_dias:
        visitas_por_dia_real[v.fecha] += 1
    
    datos_semana = get_visitas_por_dia()
    
    return render_template('panel/estadisticas.html',
                         visitas=get_estadisticas(),
                         visitas_ayer=visitas_ayer,
                         visitas_hoy=visitas_hoy,
                         visitas_por_dia_real=visitas_por_dia_real,
                         datos_semana=datos_semana,
                         categorias=get_categorias(),
                         categorias_lista=get_categorias_lista(),
                         usuario=session.get('nombre'))

@app.route('/panel/notificaciones')
@login_required
def panel_notificaciones():
    return render_template('panel/notificaciones.html', usuario=session.get('nombre'))

# ========== RUTAS PÚBLICAS ==========
@app.route('/')
def index():
    actualizar_estadisticas('inicio')
    
    productos_destacados = Producto.query.filter_by(destacado=True).all()
    categoria_actual = request.args.get('categoria')
    if categoria_actual:
        categoria_actual = escape(categoria_actual)
    
    return render_template('index.html', 
                         productos=productos_destacados,
                         categorias=get_categorias_lista(),
                         categoria_actual=categoria_actual)

@app.route('/nosotros')
def nosotros():
    actualizar_estadisticas('nosotros')
    return render_template('nosotros.html', categorias=get_categorias_lista())

@app.route('/contacto')
def contacto():
    actualizar_estadisticas('contacto')
    return render_template('contacto.html', categorias=get_categorias_lista())

@app.route('/enviar-contacto', methods=['POST'])
def enviar_contacto():
    flash('Mensaje enviado correctamente', 'success')
    return redirect(url_for('contacto'))

@app.route('/enviar-contacto-filtro', methods=['POST'])
def enviar_contacto_filtro():
    flash('Mensaje enviado correctamente. Te responderemos a la brevedad.', 'success')
    return redirect(url_for('contacto'))

@app.route('/faq')
def faq():
    actualizar_estadisticas('faq')
    return render_template('faq.html', categorias=get_categorias_lista())

@app.route('/mapa-web')
def mapaweb():
    actualizar_estadisticas('mapa-web')
    return render_template('mapaweb.html', categorias=get_categorias_lista())

@app.route('/cookies')
def cookies():
    actualizar_estadisticas('cookies')
    return render_template('cookies.html', categorias=get_categorias_lista())

@app.route('/terminos')
def terminos():
    actualizar_estadisticas('terminos')
    return render_template('terminos.html', categorias=get_categorias_lista())

@app.route('/privacidad')
def privacidad():
    actualizar_estadisticas('privacidad')
    return render_template('privacidad.html', categorias=get_categorias_lista())

@app.route('/legal')
def legal():
    actualizar_estadisticas('legal')
    return render_template('legal.html', categorias=get_categorias_lista())

@app.route('/categorias')
def categorias_page():
    actualizar_estadisticas('categorias')
    return render_template('categorias.html', 
                         categorias=get_categorias_lista(),
                         productos=Producto.query.all())

@app.route('/categoria/<nombre>')
def categoria(nombre):
    nombre_limpio = escape(nombre)
    if nombre_limpio not in get_categorias_lista():
        abort(404)
    
    actualizar_estadisticas(f'categoria_{nombre_limpio}')
    productos_filtrados = Producto.query.filter_by(categoria=nombre_limpio).all()
    
    return render_template('index.html', 
                         productos=productos_filtrados,
                         categoria_actual=nombre_limpio,
                         categorias=get_categorias_lista())

@app.route('/buscar')
def buscar():
    query_raw = request.args.get('q', '')
    query = escape(query_raw).lower()
    
    if not query or len(query) < 2:
        flash('Ingresa al menos 2 caracteres para buscar', 'info')
        return redirect(url_for('index'))
    
    actualizar_estadisticas('buscar')
    
    productos_filtrados = Producto.query.filter(
        (Producto.titulo.ilike(f'%{query}%')) | (Producto.descripcion.ilike(f'%{query}%'))
    ).all()
    
    return render_template('index.html', 
                         productos=productos_filtrados,
                         busqueda=query,
                         categorias=get_categorias_lista())

@app.route('/recomendados')
def recomendados():
    actualizar_estadisticas('recomendados')
    return render_template('index.html', 
                         productos=Producto.query.all(),
                         categorias=get_categorias_lista())

# ========== API ENDPOINTS ==========
@app.route('/api/registrar-click', methods=['POST'])
def registrar_click():
    data = request.get_json()
    producto_id = data.get('producto_id')
    
    producto = Producto.query.get(producto_id)
    if producto:
        producto.clics += 1
        db.session.commit()
        return jsonify({'status': 'ok', 'clics': producto.clics}), 200
    
    return jsonify({'status': 'error', 'message': 'Producto no encontrado'}), 404

@app.route('/api/productos/<int:producto_id>', methods=['GET'])
def api_producto(producto_id):
    producto = Producto.query.get(producto_id)
    if producto:
        return jsonify({
            'id': producto.id,
            'titulo': producto.titulo,
            'descripcion': producto.descripcion,
            'categoria': producto.categoria,
            'destacado': producto.destacado,
            'clics': producto.clics
        })
    return jsonify({'error': 'Producto no encontrado'}), 404

@app.route('/api/categorias', methods=['GET'])
def api_categorias():
    return jsonify(get_categorias_lista())

@app.route('/api/estadisticas', methods=['GET'])
def api_estadisticas():
    stats = get_estadisticas()
    return jsonify({
        'total': stats['total'],
        'hoy': stats['hoy'],
        'semana': stats['semana'],
        'mes': stats['mes']
    })

@app.route('/api/buscar', methods=['GET'])
def api_buscar():
    query = request.args.get('q', '').lower()
    if not query:
        return jsonify([])
    
    query_limpia = escape(query)
    
    productos = Producto.query.filter(
        (Producto.titulo.ilike(f'%{query_limpia}%')) | (Producto.descripcion.ilike(f'%{query_limpia}%'))
    ).limit(10).all()
    
    resultados = [
        {'id': p.id, 'titulo': p.titulo, 'categoria': p.categoria}
        for p in productos
    ]
    
    return jsonify(resultados)

# ========== ROBOTS.TXT Y SITEMAP ==========
@app.route('/robot.txt')
def robot_txt():
    config = get_config()
    dominio_limpio = escape(config.get('dominio', 'miafiliado.com'))
    lines = [
        "User-agent: *",
        "Disallow: /panel/",
        "Disallow: /api/",
        "Allow: /",
        f"Sitemap: https://{dominio_limpio}/sitemap.xml",
    ]
    return "\n".join(lines), 200, {'Content-Type': 'text/plain'}

@app.route('/sitemap.xml')
def sitemap():
    config = get_config()
    dominio_limpio = escape(config.get('dominio', 'miafiliado.com'))
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n'
    xml += '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    
    paginas = ['', 'nosotros', 'contacto', 'faq', 'mapa-web', 'cookies', 'terminos', 'privacidad', 'legal', 'categorias', 'recomendados']
    
    for pagina in paginas:
        xml += f'  <url>\n'
        xml += f'    <loc>https://{dominio_limpio}/{pagina}</loc>\n'
        xml += f'    <lastmod>{datetime.now().strftime("%Y-%m-%d")}</lastmod>\n'
        xml += f'    <changefreq>weekly</changefreq>\n'
        xml += f'    <priority>0.8</priority>\n'
        xml += f'  </url>\n'
    
    for cat in get_categorias_lista():
        xml += f'  <url>\n'
        xml += f'    <loc>https://{dominio_limpio}/categoria/{cat}</loc>\n'
        xml += f'    <lastmod>{datetime.now().strftime("%Y-%m-%d")}</lastmod>\n'
        xml += f'    <changefreq>weekly</changefreq>\n'
        xml += f'    <priority>0.7</priority>\n'
        xml += f'  </url>\n'
    
    xml += '</urlset>'
    return xml, 200, {'Content-Type': 'application/xml'}

# ========== MANEJADORES DE ERRORES ==========
@app.errorhandler(404)
def not_found_error(error):
    return render_template('404.html', 
                         categorias=get_categorias_lista(),
                         request=request), 404

@app.errorhandler(400)
def bad_request_error(error):
    return render_template('400.html', 
                         categorias=get_categorias_lista(),
                         request=request), 400

@app.errorhandler(500)
def internal_error(error):
    error_id = f"ERR-{random.randint(1000, 9999)}"
    logger.error(f"Error 500 - ID: {error_id} - {error}")
    return render_template('500.html', 
                         categorias=get_categorias_lista(),
                         request=request,
                         error_id=error_id), 500



@app.route('/panel/categorias/editar/<categoria_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def panel_categoria_editar(categoria_id):
    """Editar una categoría existente"""
    actualizar_estadisticas('panel_categoria_editar')
    
    categoria_id_limpio = escape(categoria_id)
    categoria = Categoria.query.filter_by(categoria_id=categoria_id_limpio).first_or_404()
    
    if request.method == 'POST':
        nuevo_nombre = escape(request.form.get('nombre', ''))
        
        if not nuevo_nombre:
            flash('El nombre no puede estar vacío', 'error')
            return redirect(url_for('panel_categoria_editar', categoria_id=categoria_id))
        
        try:
            categoria.nombre = nuevo_nombre
            db.session.commit()
            logger.info(f"Categoría actualizada: {categoria_id}")
            flash('Categoría actualizada correctamente', 'success')
            return redirect(url_for('panel_categorias'))
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error actualizando categoría: {e}")
            flash('Error al actualizar la categoría', 'error')
    
    return render_template('panel/categoria_editar.html',
                         categoria=categoria,
                         categorias=get_categorias(),
                         categorias_lista=get_categorias_lista(),
                         usuario=session.get('nombre'))



# ========== TRADUCTOR ==========
@app.route('/set-language/<lang>')
def set_language(lang):
    lang_limpio = escape(lang)
    session['language'] = lang_limpio
    return redirect(request.referrer or url_for('index'))

# ========== INICIALIZACIÓN Y EJECUCIÓN ==========
if __name__ == '__main__':
    with app.app_context():
        # Inicializar base de datos si no existe
        if not os.path.exists('filtro_amazon.db') and 'sqlite' in app.config['SQLALCHEMY_DATABASE_URI']:
            init_db()
            print("✅ Base de datos SQLite creada por primera vez")
        elif 'postgresql' in app.config['SQLALCHEMY_DATABASE_URI'] or 'mysql' in app.config['SQLALCHEMY_DATABASE_URI']:
            # Para bases de datos no SQLite, solo crear tablas si no existen
            db.create_all()
            print("✅ Conexión a base de datos establecida")
    
    if not IS_PRODUCTION:
        print("=" * 60)
        print("🚀 FILTRO AMAZON - MODO DESARROLLO")
        print("=" * 60)
        print(f"📊 PANEL: http://localhost:5000/panel/login")
        print("👤 Usuarios: admin/admin123, editor/editor123")
        print("-" * 60)
        print("🔒 MEDIDAS DE SEGURIDAD ACTIVADAS:")
        print("   ✅ Base de datos configurada")
        print("   ✅ Contraseñas cifradas")
        print("   ✅ Límite de intentos de login")
        print("   ✅ Escape XSS")
        print("   ✅ Validación de entradas")
        print("   ✅ Cookies seguras")
        print("   ✅ CSRF Protection")
        print("   ✅ Logging profesional")
        print("=" * 60)
        
        # Solo ejecutar servidor de desarrollo si no estamos en producción
        app.run(host='0.0.0.0', port=5000, debug=True)
    else:
        print("=" * 60)
        print("✅ APLICACIÓN LISTA PARA PRODUCCIÓN")
        print("=" * 60)
        print("📊 Usa Gunicorn o el servidor WSGI de tu hosting:")
        print("   gunicorn -w 4 app:app")
        print("=" * 60)