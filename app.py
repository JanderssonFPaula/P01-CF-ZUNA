from flask import Flask, render_template, request, jsonify, redirect, url_for, session, flash, send_from_directory
from datetime import datetime
import os
import hashlib
import secrets
import re
import tempfile
import requests
from dotenv import load_dotenv
from supabase import create_client, Client
from functools import wraps

# Carrega o .env sempre a partir da pasta do próprio app.py
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_BASE_DIR, '.env'), override=True)

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', secrets.token_hex(32))

# ============================================================
# SUPABASE - Configuração via .env (não via interface)
# ============================================================
TABLE_PREFIX = "p01cf_"
TABLE_CONTAS      = f"{TABLE_PREFIX}contas"
TABLE_TRANSACOES  = f"{TABLE_PREFIX}transacoes"
TABLE_LISTAS      = f"{TABLE_PREFIX}listas_compras"
TABLE_ITENS       = f"{TABLE_PREFIX}itens_lista"
TABLE_USUARIOS    = f"{TABLE_PREFIX}usuarios"

supabase: Client = None

# Inicializa imediatamente ao carregar o módulo
def init_supabase():
    global supabase
    url = os.getenv('SUPABASE_URL')
    key = os.getenv('SUPABASE_KEY')
    if not url or not key:
        print("ERRO: SUPABASE_URL e SUPABASE_KEY não encontrados no .env")
        return False
    try:
        supabase = create_client(supabase_url=url, supabase_key=key)
        return True
    except Exception as e:
        print(f"Erro ao conectar ao Supabase: {e}")
        return False

# Chamar imediatamente ao iniciar o módulo
init_supabase()

# ============================================================
# EVOLUTION API (WHATSAPP)
# ============================================================
def _first_env(*keys):
    for key in keys:
        value = os.getenv(key)
        if value:
            return value.strip()
    return None

def get_evolution_config():
    return {
        'url': _first_env('EVOLUTION_URL', 'evolutionurl'),
        'instance': _first_env(
            'EVOLUTION_INSTANCE',
            'EVOLUTION_INTANCE',
            'evolutioninstance',
            'evolutionintance'
        ),
        'token': _first_env('EVOLUTION_TOKEN', 'evolutiontoken')
    }

def normalizar_numero_whatsapp(numero):
    return ''.join(ch for ch in (numero or '') if ch.isdigit())

def moeda_br(valor):
    return f"R$ {float(valor):.2f}".replace('.', ',')

def _limpar_linha_ocr(linha):
    texto = (linha or '').strip()
    texto = re.sub(r'\s+', ' ', texto)
    return texto

def _parse_br_number(raw_value):
    value = (raw_value or '').strip()
    value = value.replace('R$', '').replace(' ', '')
    value = value.replace('.', '').replace(',', '.')
    try:
        return float(value)
    except ValueError:
        return None

def _extrair_itens_nota_por_texto(raw_text):
    itens = []
    ignorar = (
        'cnpj', 'cpf', 'ie', 'cupom', 'fiscal', 'cliente', 'subtotal',
        'desconto', 'acrescimo', 'troco', 'pagamento', 'dinheiro', 'cartao',
        'pix', 'total', 'valor total', 'qrcode', 'chave de acesso', 'nfce',
        'coo', 'operador', 'caixa'
    )

    pattern_qtd_x_unit_total = re.compile(
        r'^(?P<descricao>.+?)\s+(?P<qtd>\d+[.,]?\d*)\s*[xX]\s*(?P<unit>\d+[.,]\d{2})\s+(?P<total>\d+[.,]\d{2})$'
    )
    pattern_qtd_x_unit = re.compile(
        r'^(?P<descricao>.+?)\s+(?P<qtd>\d+[.,]?\d*)\s*[xX]\s*(?P<unit>\d+[.,]\d{2})$'
    )
    pattern_desc_total = re.compile(
        r'^(?P<descricao>[A-Za-z0-9\s\-\.,/%\(\)]+?)\s+(?P<total>\d+[.,]\d{2})$'
    )

    for raw_line in (raw_text or '').splitlines():
        line = _limpar_linha_ocr(raw_line)
        if len(line) < 4:
            continue

        line_lower = line.lower()
        if any(token in line_lower for token in ignorar):
            continue

        if sum(ch.isdigit() for ch in line) < 2:
            continue

        item = None

        m = pattern_qtd_x_unit_total.match(line)
        if m:
            descricao = m.group('descricao').strip(' -')
            qtd = _parse_br_number(m.group('qtd')) or 1.0
            unit = _parse_br_number(m.group('unit'))
            if descricao and unit and qtd > 0:
                item = {
                    'descricao': descricao[:120],
                    'quantidade': int(round(qtd)) if qtd >= 1 else 1,
                    'valor': float(unit)
                }

        if item is None:
            m = pattern_qtd_x_unit.match(line)
            if m:
                descricao = m.group('descricao').strip(' -')
                qtd = _parse_br_number(m.group('qtd')) or 1.0
                unit = _parse_br_number(m.group('unit'))
                if descricao and unit and qtd > 0:
                    item = {
                        'descricao': descricao[:120],
                        'quantidade': int(round(qtd)) if qtd >= 1 else 1,
                        'valor': float(unit)
                    }

        if item is None:
            m = pattern_desc_total.match(line)
            if m:
                descricao = m.group('descricao').strip(' -')
                total = _parse_br_number(m.group('total'))
                if descricao and total and total > 0:
                    item = {
                        'descricao': descricao[:120],
                        'quantidade': 1,
                        'valor': float(total)
                    }

        if item is None:
            continue

        if len(item['descricao']) < 3:
            continue

        itens.append(item)

    dedup = []
    seen = set()
    for item in itens:
        key = (item['descricao'].lower(), item['quantidade'], round(item['valor'], 2))
        if key in seen:
            continue
        seen.add(key)
        dedup.append(item)

    return dedup[:60]

def _extrair_itens_por_ocr(arquivo_stream, filename):
    try:
        from PIL import Image, ImageOps
        import pytesseract
    except ImportError as e:
        raise RuntimeError(
            'Dependencias OCR ausentes. Instale com: pip install pillow pytesseract'
        ) from e

    suffix = os.path.splitext(filename or '')[1].lower() or '.jpg'
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(arquivo_stream.read())
        tmp_path = tmp.name

    try:
        img = Image.open(tmp_path)
        img = ImageOps.grayscale(img)
        texto = pytesseract.image_to_string(img, lang=os.getenv('OCR_LANG', 'por+eng'))
    except Exception as e:
        raise RuntimeError(f'Erro ao processar OCR da imagem: {e}') from e
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    itens = _extrair_itens_nota_por_texto(texto)
    if not itens:
        raise RuntimeError('Nao consegui identificar itens na nota. Tente uma foto mais nitida.')
    return itens

def enviar_texto_whatsapp(numero, mensagem):
    cfg = get_evolution_config()
    if not cfg['url'] or not cfg['instance'] or not cfg['token']:
        raise ValueError('Credenciais da Evolution API nao configuradas no .env.')

    endpoint = _first_env('EVOLUTION_SEND_ENDPOINT', 'evolution_send_endpoint') or 'message/sendText'
    url = f"{cfg['url'].rstrip('/')}/{endpoint.strip('/')}/{cfg['instance']}"

    payload = {
        'number': numero,
        'text': mensagem,
        'delay': 0,
        'linkPreview': False
    }
    headers = {
        'apikey': cfg['token'],
        'Content-Type': 'application/json'
    }

    response = requests.post(url, json=payload, headers=headers, timeout=20)
    if response.status_code >= 400:
        raise RuntimeError(f"Evolution API {response.status_code}: {response.text[:250]}")

def montar_relatorio_geral(user_id):
    contas = supabase.table(TABLE_CONTAS)\
        .select('id,nome,banco,categoria,saldo')\
        .eq('user_id', user_id)\
        .order('categoria').order('nome').execute()

    contas_data = contas.data or []
    total = sum(float(c['saldo']) for c in contas_data)

    linhas = [
        '*Relatorio Financeiro (Geral)*',
        f"Data: {datetime.now().strftime('%d/%m/%Y %H:%M')}",
        f"Total geral: {moeda_br(total)}",
        f"Qtd. contas: {len(contas_data)}",
        ''
    ]

    if not contas_data:
        linhas.append('Nenhuma conta cadastrada.')
    else:
        linhas.append('*Contas:*')
        for conta in contas_data:
            linhas.append(
                f"- {conta['nome']} ({conta['banco']}) [{conta['categoria']}]: {moeda_br(conta['saldo'])}"
            )

    return '\n'.join(linhas)

def montar_relatorio_conta(user_id, conta_id):
    conta = supabase.table(TABLE_CONTAS)\
        .select('*').eq('id', conta_id)\
        .eq('user_id', user_id)\
        .single().execute()

    if not conta.data:
        raise ValueError('Conta nao encontrada.')

    transacoes = supabase.table(TABLE_TRANSACOES)\
        .select('*').eq('conta_id', conta_id)\
        .order('data', desc=True).limit(10).execute()

    trans_data = transacoes.data or []
    entradas = sum(float(t['valor']) for t in trans_data if t['tipo'] == 'entrada')
    saidas = sum(float(t['valor']) for t in trans_data if t['tipo'] == 'saida')

    linhas = [
        '*Relatorio Financeiro (Conta)*',
        f"Data: {datetime.now().strftime('%d/%m/%Y %H:%M')}",
        f"Conta: {conta.data['nome']}",
        f"Banco: {conta.data['banco']}",
        f"Categoria: {conta.data['categoria']}",
        f"Saldo atual: {moeda_br(conta.data['saldo'])}",
        f"Ultimas {len(trans_data)} transacoes: entradas {moeda_br(entradas)} | saidas {moeda_br(saidas)}",
        ''
    ]

    if not trans_data:
        linhas.append('Sem transacoes recentes.')
    else:
        linhas.append('*Movimentacoes recentes:*')
        for t in trans_data:
            sinal = '+' if t['tipo'] == 'entrada' else '-'
            descricao = t.get('descricao') or 'Sem descricao'
            data = (t.get('data') or '')[:16]
            linhas.append(f"- {data} | {descricao}: {sinal}{moeda_br(t['valor'])}")

    return '\n'.join(linhas)

def montar_relatorio_lista(user_id, lista_id):
    lista = supabase.table(TABLE_LISTAS)\
        .select('*').eq('id', lista_id)\
        .eq('user_id', user_id)\
        .single().execute()

    if not lista.data:
        raise ValueError('Lista nao encontrada.')

    itens = supabase.table(TABLE_ITENS)\
        .select('*').eq('lista_id', lista_id).execute()

    itens_data = itens.data or []
    total = sum(float(i['valor']) * int(i['quantidade']) for i in itens_data)

    status = 'Concluida' if lista.data.get('concluida') else 'Pendente'
    linhas = [
        '*Relatorio de Lista de Compras*',
        f"Data: {datetime.now().strftime('%d/%m/%Y %H:%M')}",
        f"Lista: {lista.data['nome']}",
        f"Status: {status}",
        f"Qtd. itens: {len(itens_data)}",
        f"Total: {moeda_br(total)}",
        ''
    ]

    if not itens_data:
        linhas.append('Sem itens na lista.')
    else:
        linhas.append('*Itens:*')
        for item in itens_data:
            subtotal = float(item['valor']) * int(item['quantidade'])
            linhas.append(
                f"- {item['descricao']}: {item['quantidade']} x {moeda_br(item['valor'])} = {moeda_br(subtotal)}"
            )

    return '\n'.join(linhas)

# ============================================================
# AUTENTICAÇÃO
# ============================================================
def hash_senha(senha):
    return hashlib.sha256(senha.encode()).hexdigest()

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            flash('Faça login para continuar.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def get_usuario_logado():
    if 'user_id' not in session:
        return None
    try:
        res = supabase.table(TABLE_USUARIOS).select('*').eq('id', session['user_id']).single().execute()
        return res.data
    except:
        return None


@app.route('/img/<path:filename>')
def img_file(filename):
    return send_from_directory(os.path.join(_BASE_DIR, 'img'), filename)

# ============================================================
# ROTAS DE AUTENTICAÇÃO
# ============================================================

@app.route('/login', methods=['GET', 'POST'])
def login():
    if supabase is None:
        return render_template('sem_config.html')
    if 'user_id' in session:
        return redirect(url_for('index'))

    if request.method == 'POST':
        email = request.form['email'].strip().lower()
        senha = hash_senha(request.form['senha'])

        try:
            res = supabase.table(TABLE_USUARIOS)\
                .select('*')\
                .eq('email', email)\
                .eq('senha', senha)\
                .single().execute()

            if res.data:
                session['user_id'] = res.data['id']
                session['user_nome'] = res.data['nome']
                flash(f'Bem-vindo, {res.data["nome"]}!', 'success')
                return redirect(url_for('index'))
            else:
                flash('Email ou senha incorretos.', 'danger')
        except:
            flash('Email ou senha incorretos.', 'danger')

    return render_template('login.html')


@app.route('/cadastro', methods=['GET', 'POST'])
def cadastro():
    if supabase is None:
        return render_template('sem_config.html')
    if 'user_id' in session:
        return redirect(url_for('index'))

    if request.method == 'POST':
        nome  = request.form['nome'].strip()
        email = request.form['email'].strip().lower()
        senha = request.form['senha']
        confirma = request.form['confirma_senha']

        # Validações
        if not nome or not email or not senha:
            flash('Preencha todos os campos.', 'danger')
            return render_template('cadastro.html')

        if senha != confirma:
            flash('As senhas não coincidem.', 'danger')
            return render_template('cadastro.html')

        if len(senha) < 6:
            flash('A senha deve ter pelo menos 6 caracteres.', 'danger')
            return render_template('cadastro.html')

        # Verificar se email já existe
        try:
            existe = supabase.table(TABLE_USUARIOS)\
                .select('id')\
                .eq('email', email)\
                .execute()

            if existe.data:
                flash('Este email já está cadastrado.', 'danger')
                return render_template('cadastro.html')

            # Criar usuário
            novo = supabase.table(TABLE_USUARIOS).insert({
                'nome': nome,
                'email': email,
                'senha': hash_senha(senha),
                'data_cadastro': datetime.now().isoformat()
            }).execute()

            if novo.data:
                session['user_id'] = novo.data[0]['id']
                session['user_nome'] = nome
                flash(f'Conta criada com sucesso! Bem-vindo, {nome}!', 'success')
                return redirect(url_for('index'))

        except Exception as e:
            flash(f'Erro ao criar conta: {str(e)}', 'danger')

    return render_template('cadastro.html')


@app.route('/logout')
def logout():
    nome = session.get('user_nome', '')
    session.clear()
    flash(f'Até logo, {nome}!', 'info')
    return redirect(url_for('login'))


@app.route('/perfil', methods=['GET', 'POST'])
@login_required
def perfil():
    usuario = get_usuario_logado()

    if request.method == 'POST':
        acao = request.form.get('acao')

        if acao == 'atualizar':
            nome  = request.form['nome'].strip()
            email = request.form['email'].strip().lower()

            # Verificar se o email já pertence a outro usuário
            try:
                existe = supabase.table(TABLE_USUARIOS)\
                    .select('id')\
                    .eq('email', email)\
                    .neq('id', session['user_id'])\
                    .execute()

                if existe.data:
                    flash('Este email já está em uso.', 'danger')
                    return render_template('perfil.html', usuario=usuario)

                supabase.table(TABLE_USUARIOS).update({
                    'nome': nome,
                    'email': email
                }).eq('id', session['user_id']).execute()

                session['user_nome'] = nome
                flash('Perfil atualizado!', 'success')
                return redirect(url_for('perfil'))

            except Exception as e:
                flash(f'Erro: {str(e)}', 'danger')

        elif acao == 'senha':
            senha_atual   = hash_senha(request.form['senha_atual'])
            nova_senha    = request.form['nova_senha']
            confirma      = request.form['confirma_nova_senha']

            if usuario['senha'] != senha_atual:
                flash('Senha atual incorreta.', 'danger')
                return render_template('perfil.html', usuario=usuario)

            if nova_senha != confirma:
                flash('As novas senhas não coincidem.', 'danger')
                return render_template('perfil.html', usuario=usuario)

            if len(nova_senha) < 6:
                flash('A nova senha deve ter pelo menos 6 caracteres.', 'danger')
                return render_template('perfil.html', usuario=usuario)

            supabase.table(TABLE_USUARIOS).update({
                'senha': hash_senha(nova_senha)
            }).eq('id', session['user_id']).execute()

            flash('Senha alterada com sucesso!', 'success')
            return redirect(url_for('perfil'))

    return render_template('perfil.html', usuario=usuario)


# ============================================================
# ROTAS PRINCIPAIS (protegidas por login)
# ============================================================

@app.route('/')
def index():
    if not session.get('user_id'):
        return render_template('landing.html')

    try:
        contas = supabase.table(TABLE_CONTAS)\
            .select('*')\
            .eq('user_id', session['user_id'])\
            .order('categoria').order('nome').execute()

        categorias  = {}
        total_geral = 0

        for conta in contas.data:
            cat = conta['categoria']
            if cat not in categorias:
                categorias[cat] = {'contas': [], 'total': 0}
            categorias[cat]['contas'].append(conta)
            categorias[cat]['total']  += float(conta['saldo'])
            total_geral               += float(conta['saldo'])

        return render_template('index.html', categorias=categorias, total_geral=total_geral)
    except Exception as e:
        flash(f'Erro ao carregar dados: {str(e)}', 'danger')
        return render_template('index.html', categorias={}, total_geral=0)


@app.route('/whatsapp/enviar-relatorio', methods=['POST'])
@login_required
def enviar_relatorio_whatsapp():
    uid = session['user_id']
    numero = normalizar_numero_whatsapp(request.form.get('numero'))
    tipo = (request.form.get('tipo') or '').strip().lower()
    referencia_id = (request.form.get('referencia_id') or '').strip()
    redirect_to = request.form.get('redirect_to') or url_for('index')

    if not numero:
        flash('Informe um numero de WhatsApp valido.', 'danger')
        return redirect(redirect_to)

    try:
        if tipo == 'geral':
            mensagem = montar_relatorio_geral(uid)
        elif tipo == 'conta':
            if not referencia_id.isdigit():
                raise ValueError('Conta invalida para relatorio.')
            mensagem = montar_relatorio_conta(uid, int(referencia_id))
        elif tipo == 'lista':
            if not referencia_id.isdigit():
                raise ValueError('Lista invalida para relatorio.')
            mensagem = montar_relatorio_lista(uid, int(referencia_id))
        else:
            raise ValueError('Tipo de relatorio invalido.')

        enviar_texto_whatsapp(numero, mensagem)
        flash('Relatorio enviado no WhatsApp com sucesso!', 'success')
    except Exception as e:
        flash(f'Falha ao enviar relatorio: {str(e)}', 'danger')

    return redirect(redirect_to)


@app.route('/conta/adicionar', methods=['POST'])
@login_required
def adicionar_conta():
    supabase.table(TABLE_CONTAS).insert({
        'user_id':   session['user_id'],
        'nome':      request.form['nome'],
        'banco':     request.form['banco'],
        'categoria': request.form['categoria'],
        'saldo':     float(request.form.get('saldo', 0)),
        'cor':       request.form.get('cor', '#007bff')
    }).execute()
    flash('Conta criada com sucesso!', 'success')
    return redirect(url_for('index'))


@app.route('/conta/<int:id>')
@login_required
def ver_conta(id):
    conta = supabase.table(TABLE_CONTAS)\
        .select('*').eq('id', id)\
        .eq('user_id', session['user_id'])\
        .single().execute()

    if not conta.data:
        flash('Conta não encontrada.', 'danger')
        return redirect(url_for('index'))

    transacoes = supabase.table(TABLE_TRANSACOES)\
        .select('*').eq('conta_id', id)\
        .order('data', desc=True).limit(50).execute()

    return render_template('conta.html', conta=conta.data, transacoes=transacoes.data)


@app.route('/conta/<int:id>/transacao', methods=['POST'])
@login_required
def adicionar_transacao(id):
    tipo  = request.form['tipo']
    valor = float(request.form['valor'])

    supabase.table(TABLE_TRANSACOES).insert({
        'conta_id':  id,
        'tipo':      tipo,
        'valor':     valor,
        'descricao': request.form['descricao']
    }).execute()

    conta = supabase.table(TABLE_CONTAS).select('saldo').eq('id', id).single().execute()
    novo  = float(conta.data['saldo']) + (valor if tipo == 'entrada' else -valor)
    supabase.table(TABLE_CONTAS).update({'saldo': novo}).eq('id', id).execute()

    flash('Transação registrada!', 'success')
    return redirect(url_for('ver_conta', id=id))


@app.route('/conta/<int:id>/editar', methods=['POST'])
@login_required
def editar_conta(id):
    supabase.table(TABLE_CONTAS).update({
        'nome':      request.form['nome'],
        'banco':     request.form['banco'],
        'categoria': request.form['categoria'],
        'cor':       request.form['cor']
    }).eq('id', id).eq('user_id', session['user_id']).execute()
    flash('Conta atualizada!', 'success')
    return redirect(url_for('ver_conta', id=id))


@app.route('/conta/<int:id>/deletar', methods=['POST'])
@login_required
def deletar_conta(id):
    try:
        uid = session['user_id']

        conta = supabase.table(TABLE_CONTAS)\
            .select('id')\
            .eq('id', id)\
            .eq('user_id', uid)\
            .single().execute()

        if not conta.data:
            flash('Conta não encontrada.', 'danger')
            return redirect(url_for('index'))

        # Remove referência da conta em listas já concluídas/associadas.
        supabase.table(TABLE_LISTAS)\
            .update({'conta_id': None})\
            .eq('user_id', uid)\
            .eq('conta_id', id).execute()

        # Remove histórico de transações da conta antes de excluir a conta.
        supabase.table(TABLE_TRANSACOES)\
            .delete().eq('conta_id', id).execute()

        supabase.table(TABLE_CONTAS)\
            .delete().eq('id', id)\
            .eq('user_id', uid).execute()

        flash('Conta deletada!', 'success')
    except Exception as e:
        flash(f'Não foi possível deletar a conta: {str(e)}', 'danger')
    return redirect(url_for('index'))


# ============================================================
# LISTAS DE COMPRAS
# ============================================================

@app.route('/listas')
@login_required
def listas_compras():
    try:
        uid = session['user_id']

        listas_ativas = supabase.table(TABLE_LISTAS)\
            .select('*').eq('user_id', uid)\
            .eq('concluida', False)\
            .order('data_criacao', desc=True).execute()

        for lista in listas_ativas.data:
            itens = supabase.table(TABLE_ITENS).select('*').eq('lista_id', lista['id']).execute()
            lista['itens_lista'] = itens.data
            lista['total'] = sum(float(i['valor']) * i['quantidade'] for i in itens.data)

        listas_concluidas = supabase.table(TABLE_LISTAS)\
            .select('*').eq('user_id', uid)\
            .eq('concluida', True)\
            .order('data_conclusao', desc=True).limit(10).execute()

        for lista in listas_concluidas.data:
            itens = supabase.table(TABLE_ITENS).select('*').eq('lista_id', lista['id']).execute()
            lista['itens_lista'] = itens.data
            lista['total'] = sum(float(i['valor']) * i['quantidade'] for i in itens.data)
            if lista.get('conta_id'):
                c = supabase.table(TABLE_CONTAS).select('nome').eq('id', lista['conta_id']).single().execute()
                lista['contas'] = c.data or {}
            else:
                lista['contas'] = {}

        return render_template('listas_compras.html',
                               listas_ativas=listas_ativas.data,
                               listas_concluidas=listas_concluidas.data)
    except Exception as e:
        flash(f'Erro: {str(e)}', 'danger')
        return redirect(url_for('index'))


@app.route('/lista/nova', methods=['POST'])
@login_required
def nova_lista():
    lista = supabase.table(TABLE_LISTAS).insert({
        'user_id': session['user_id'],
        'nome': request.form['nome']
    }).execute()
    flash('Lista criada!', 'success')
    return redirect(url_for('ver_lista', id=lista.data[0]['id']))


@app.route('/lista/<int:id>')
@login_required
def ver_lista(id):
    lista = supabase.table(TABLE_LISTAS)\
        .select('*').eq('id', id)\
        .eq('user_id', session['user_id'])\
        .single().execute()

    if not lista.data:
        flash('Lista não encontrada.', 'danger')
        return redirect(url_for('listas_compras'))

    itens  = supabase.table(TABLE_ITENS).select('*').eq('lista_id', id).execute()
    total  = sum(float(i['valor']) * i['quantidade'] for i in itens.data)
    contas = supabase.table(TABLE_CONTAS)\
        .select('*').eq('user_id', session['user_id']).execute()

    return render_template('lista_detalhe.html',
                           lista=lista.data, itens=itens.data,
                           total=total, contas=contas.data)


@app.route('/lista/<int:id>/item', methods=['POST'])
@login_required
def adicionar_item_lista(id):
    lista = supabase.table(TABLE_LISTAS)\
        .select('id, concluida')\
        .eq('id', id)\
        .eq('user_id', session['user_id'])\
        .single().execute()

    if not lista.data:
        flash('Lista nao encontrada.', 'danger')
        return redirect(url_for('listas_compras'))

    if lista.data.get('concluida'):
        flash('Nao e possivel adicionar item em lista concluida.', 'warning')
        return redirect(url_for('ver_lista', id=id))

    supabase.table(TABLE_ITENS).insert({
        'lista_id':   id,
        'descricao':  request.form['descricao'],
        'valor':      float(request.form['valor']),
        'quantidade': int(request.form.get('quantidade', 1))
    }).execute()
    flash('Item adicionado!', 'success')
    return redirect(url_for('ver_lista', id=id))


@app.route('/lista/<int:id>/importar-nota', methods=['POST'])
@login_required
def importar_nota_lista(id):
    lista = supabase.table(TABLE_LISTAS)\
        .select('id, concluida')\
        .eq('id', id)\
        .eq('user_id', session['user_id'])\
        .single().execute()

    if not lista.data:
        flash('Lista nao encontrada.', 'danger')
        return redirect(url_for('listas_compras'))

    if lista.data.get('concluida'):
        flash('Nao e possivel importar nota em lista concluida.', 'warning')
        return redirect(url_for('ver_lista', id=id))

    arquivo = request.files.get('nota_fiscal')
    if not arquivo or not arquivo.filename:
        flash('Selecione uma imagem da nota fiscal.', 'warning')
        return redirect(url_for('ver_lista', id=id))

    extensoes = ('.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tif', '.tiff')
    if not arquivo.filename.lower().endswith(extensoes):
        flash('Formato invalido. Use JPG, PNG, WEBP, BMP ou TIFF.', 'danger')
        return redirect(url_for('ver_lista', id=id))

    try:
        itens_extraidos = _extrair_itens_por_ocr(arquivo.stream, arquivo.filename)
    except Exception as e:
        flash(f'Falha ao ler nota fiscal: {str(e)}', 'danger')
        return redirect(url_for('ver_lista', id=id))

    payload = []
    for item in itens_extraidos:
        payload.append({
            'lista_id': id,
            'descricao': item['descricao'],
            'valor': float(item['valor']),
            'quantidade': int(item['quantidade'])
        })

    if not payload:
        flash('Nenhum item valido foi extraido da nota.', 'warning')
        return redirect(url_for('ver_lista', id=id))

    supabase.table(TABLE_ITENS).insert(payload).execute()
    flash(f'{len(payload)} itens adicionados automaticamente pela nota fiscal.', 'success')
    return redirect(url_for('ver_lista', id=id))


@app.route('/lista/<int:id>/item/<int:item_id>/deletar', methods=['POST'])
@login_required
def deletar_item_lista(id, item_id):
    lista = supabase.table(TABLE_LISTAS)\
        .select('id, concluida')\
        .eq('id', id)\
        .eq('user_id', session['user_id'])\
        .single().execute()

    if not lista.data:
        flash('Lista nao encontrada.', 'danger')
        return redirect(url_for('listas_compras'))

    if lista.data.get('concluida'):
        flash('Nao e possivel remover item de lista concluida.', 'warning')
        return redirect(url_for('ver_lista', id=id))

    supabase.table(TABLE_ITENS)\
        .delete().eq('id', item_id)\
        .eq('lista_id', id).execute()
    flash('Item removido!', 'success')
    return redirect(url_for('ver_lista', id=id))


@app.route('/lista/<int:id>/item/<int:item_id>/editar', methods=['POST'])
@login_required
def editar_item_lista(id, item_id):
    lista = supabase.table(TABLE_LISTAS)\
        .select('id, concluida')\
        .eq('id', id)\
        .eq('user_id', session['user_id'])\
        .single().execute()

    if not lista.data:
        flash('Lista nao encontrada.', 'danger')
        return redirect(url_for('listas_compras'))

    if lista.data.get('concluida'):
        flash('Nao e possivel editar item de lista concluida.', 'warning')
        return redirect(url_for('ver_lista', id=id))

    descricao = request.form['descricao'].strip()
    quantidade = int(request.form.get('quantidade', 1))
    valor = float(request.form.get('valor', 0))

    if not descricao:
        flash('Descricao do item e obrigatoria.', 'danger')
        return redirect(url_for('ver_lista', id=id))

    if quantidade < 1:
        flash('Quantidade deve ser maior que zero.', 'danger')
        return redirect(url_for('ver_lista', id=id))

    if valor < 0:
        flash('Valor nao pode ser negativo.', 'danger')
        return redirect(url_for('ver_lista', id=id))

    supabase.table(TABLE_ITENS).update({
        'descricao': descricao,
        'quantidade': quantidade,
        'valor': valor
    }).eq('id', item_id).eq('lista_id', id).execute()

    flash('Item atualizado!', 'success')
    return redirect(url_for('ver_lista', id=id))


@app.route('/lista/<int:id>/pagar', methods=['POST'])
@login_required
def pagar_lista(id):
    conta_id = int(request.form['conta_id'])
    uid      = session['user_id']

    lista = supabase.table(TABLE_LISTAS).select('*').eq('id', id).eq('user_id', uid).single().execute()
    if not lista.data:
        flash('Lista nao encontrada.', 'danger')
        return redirect(url_for('listas_compras'))

    if lista.data.get('concluida'):
        flash('Essa lista ja foi concluida.', 'warning')
        return redirect(url_for('ver_lista', id=id))

    itens = supabase.table(TABLE_ITENS).select('*').eq('lista_id', id).execute()
    itens_lista = itens.data or []

    selected_raw = request.form.get('selected_item_ids', '').strip()
    selected_ids = set()
    if selected_raw:
        for raw in selected_raw.split(','):
            raw = raw.strip()
            if raw.isdigit():
                selected_ids.add(int(raw))

    if selected_ids:
        itens_pagamento = [i for i in itens_lista if int(i['id']) in selected_ids]
        if not itens_pagamento:
            flash('Selecione ao menos um item para pagar.', 'warning')
            return redirect(url_for('ver_lista', id=id))
    else:
        itens_pagamento = itens_lista

    total = sum(float(i['valor']) * i['quantidade'] for i in itens_pagamento)
    if total <= 0:
        flash('Nao ha valor valido para pagamento.', 'warning')
        return redirect(url_for('ver_lista', id=id))

    conta = supabase.table(TABLE_CONTAS)\
        .select('*').eq('id', conta_id)\
        .eq('user_id', uid).single().execute()

    if not conta.data:
        flash('Conta nao encontrada.', 'danger')
        return redirect(url_for('ver_lista', id=id))

    if float(conta.data['saldo']) < total:
        flash('Saldo insuficiente nesta conta!', 'danger')
        return redirect(url_for('ver_lista', id=id))

    desc = 'Lista: ' + lista.data['nome']
    supabase.table(TABLE_TRANSACOES).insert({
        'conta_id': conta_id, 'tipo': 'saida', 'valor': total, 'descricao': desc
    }).execute()

    # Se o usuario marcou apenas parte dos itens, remove os nao selecionados.
    if selected_ids:
        ids_selecionados = {int(i['id']) for i in itens_pagamento}
        ids_nao_selecionados = [int(i['id']) for i in itens_lista if int(i['id']) not in ids_selecionados]
        if ids_nao_selecionados:
            supabase.table(TABLE_ITENS).delete().eq('lista_id', id).in_('id', ids_nao_selecionados).execute()

    supabase.table(TABLE_CONTAS).update({'saldo': float(conta.data['saldo']) - total}).eq('id', conta_id).execute()
    supabase.table(TABLE_LISTAS).update({
        'concluida': True, 'conta_id': conta_id,
        'data_conclusao': datetime.now().isoformat()
    }).eq('id', id).execute()

    flash(f'Lista paga! R$ {total:.2f} debitado de {conta.data["nome"]}', 'success')
    return redirect(url_for('listas_compras'))


@app.route('/lista/<int:id>/deletar', methods=['POST'])
@login_required
def deletar_lista(id):
    uid = session['user_id']
    try:
        lista = supabase.table(TABLE_LISTAS)\
            .select('id')\
            .eq('id', id)\
            .eq('user_id', uid).single().execute()

        if not lista.data:
            flash('Lista nao encontrada.', 'danger')
            return redirect(url_for('listas_compras'))

        supabase.table(TABLE_ITENS).delete().eq('lista_id', id).execute()
        supabase.table(TABLE_LISTAS).delete().eq('id', id).eq('user_id', uid).execute()
        flash('Lista deletada!', 'success')
    except Exception as e:
        flash(f'Nao foi possivel deletar a lista: {str(e)}', 'danger')
    return redirect(url_for('listas_compras'))


# ============================================================
# INICIALIZAÇÃO
# ============================================================
if __name__ == '__main__':
    if init_supabase():
        print("✅ Supabase conectado!")
    else:
        print("❌ Erro: Configure SUPABASE_URL e SUPABASE_KEY no arquivo .env")
    app.run(debug=True, host='0.0.0.0', port=5000)
