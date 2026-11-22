import csv
import io
import unicodedata
import uuid
from datetime import date, datetime
import streamlit as st
import streamlit.components.v1 as components
from supabase import Client, create_client

st.set_page_config(
    page_title="Split Coin",
    page_icon="🪙",
    layout="centered",
)


# --- Funções auxiliares de formatação e validação ---

def formatar_moeda(valor: float) -> str:
    v = float(valor)
    sinal = "-" if v < -0.0001 else ""
    texto = f"{abs(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{sinal}R$ {texto}"


def parse_valor_br(texto: str) -> float | None:
    if not texto:
        return None
    limpo = texto.strip().replace("R$", "").replace(" ", "")
    if not limpo:
        return None
    if "," in limpo and "." in limpo:
        if limpo.rfind(",") > limpo.rfind("."):
            limpo = limpo.replace(".", "").replace(",", ".")
        else:
            limpo = limpo.replace(",", "")
    elif "," in limpo:
        limpo = limpo.replace(",", ".")
    try:
        val = round(float(limpo), 2)
        return val if val > 0 else None
    except ValueError:
        return None


def formatar_data_br(data_iso: str) -> str:
    if not data_iso:
        return "—"
    partes = str(data_iso)[:10].split("-")
    if len(partes) == 3:
        return f"{partes[2]}/{partes[1]}/{partes[0]}"
    return str(data_iso)


def validar_apenas_emoji(texto: str) -> str | None:
    if not texto:
        return None
    s = texto.strip()
    if not s or len(s) > 14:
        return None

    tem_base_emoji = False
    for i, ch in enumerate(s):
        cat = unicodedata.category(ch)
        if cat == "So":
            tem_base_emoji = True
        elif cat == "Sk":
            continue
        elif ch in ("\u200d", "\ufe0f", "\ufe0e"):
            continue
        elif ch == "\u20e3":
            tem_base_emoji = True
        elif ch in "0123456789#*" and i + 1 < len(s) and s[i + 1] in ("\ufe0f", "\u20e3"):
            continue
        else:
            return None

    return s if tem_base_emoji else None


def gerar_csv_extrato(membros: list[dict], despesas: list[dict]) -> bytes:
    output = io.StringIO()
    writer = csv.writer(output, delimiter=";", quoting=csv.QUOTE_MINIMAL)
    mapa_membros = {m["id"]: m["nome"] for m in membros}

    nomes_colunas_membros = []
    contagem_nomes = {}
    for m in membros:
        base = m["nome"]
        contagem_nomes[base] = contagem_nomes.get(base, 0) + 1
        if contagem_nomes[base] > 1:
            nomes_colunas_membros.append(f"{base} ({contagem_nomes[base]})")
        else:
            nomes_colunas_membros.append(base)

    cabecalho = ["Tipo", "Descrição", "Data", "Pagador", "Valor Total"] + nomes_colunas_membros
    writer.writerow(cabecalho)

    labels_tipo = {
        "despesa": "Despesa",
        "transferencia": "Transferência",
        "quitacao": "Quitação",
        "estorno_quitacao": "Estorno de Quitação",
    }

    for d in despesas:
        val = float(d["valor"])
        pagador_nome = mapa_membros.get(d["pago_por_membro_id"], "Desconhecido")
        data_br = formatar_data_br(d["data_despesa"])
        tipo_label = labels_tipo.get(d.get("tipo", "despesa"), "Despesa")

        rateios = d.get("despesa_rateios", [])
        pesos_por_membro = {r["membro_id"]: int(float(r["peso"])) for r in rateios}
        total_p = sum(pesos_por_membro.values())

        cotas = []
        for m in membros:
            p = pesos_por_membro.get(m["id"], 0)
            cota = (val * (p / total_p)) if (p > 0 and total_p > 0) else 0.0
            cotas.append(f"{cota:.2f}".replace(".", ","))

        linha = [
            tipo_label,
            d["descricao"],
            data_br,
            pagador_nome,
            f"{val:.2f}".replace(".", ","),
        ] + cotas
        writer.writerow(linha)

    return output.getvalue().encode("utf-8-sig")


# --- Persistência de Sessão no Navegador (F5) e Conexão ---

def sincronizar_cookie_sessao():
    acao = st.session_state.pop("cookie_action", None)
    if not acao:
        return
    if acao["op"] == "set":
        token = acao["token"]
        components.html(
            f'<script>document.cookie = "splitcoin_rt={token}; path=/; max-age=2592000; SameSite=Lax";</script>',
            height=0,
        )
    elif acao["op"] == "clear":
        components.html(
            '<script>document.cookie = "splitcoin_rt=; path=/; max-age=0; SameSite=Lax";</script>',
            height=0,
        )


def get_supabase() -> Client:
    client = create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])

    if st.session_state.get("sb_session"):
        sess = st.session_state.sb_session
        try:
            res = client.auth.set_session(sess["access_token"], sess["refresh_token"])
            if res and res.session and res.session.refresh_token != sess["refresh_token"]:
                st.session_state.sb_session = {
                    "access_token": res.session.access_token,
                    "refresh_token": res.session.refresh_token,
                }
                st.session_state.cookie_action = {
                    "op": "set",
                    "token": res.session.refresh_token,
                }
            return client
        except Exception:
            st.session_state.sb_session = None
            st.session_state.sb_user = None
            st.session_state.cookie_action = {"op": "clear"}

    try:
        rt_cookie = st.context.cookies.get("splitcoin_rt")
    except Exception:
        rt_cookie = None

    if rt_cookie and not st.session_state.get("sb_user"):
        try:
            res = client.auth.refresh_session(rt_cookie)
            if res and res.session and res.user:
                st.session_state.sb_session = {
                    "access_token": res.session.access_token,
                    "refresh_token": res.session.refresh_token,
                }
                st.session_state.sb_user = {
                    "id": res.user.id,
                    "email": res.user.email,
                    "nome": (res.user.user_metadata or {}).get("nome")
                    or res.user.email.split("@")[0],
                }
                st.session_state.cookie_action = {
                    "op": "set",
                    "token": res.session.refresh_token,
                }
        except Exception:
            st.session_state.cookie_action = {"op": "clear"}

    return client


def calcular_acerto(membros: list[dict], despesas: list[dict]):
    saldos = {m["id"]: 0.0 for m in membros}

    for d in despesas:
        rateios = d.get("despesa_rateios", [])
        total_pesos = sum(int(float(r["peso"])) for r in rateios)
        if total_pesos <= 0:
            continue

        valor = float(d["valor"])
        pagador_id = d["pago_por_membro_id"]
        if pagador_id in saldos:
            saldos[pagador_id] += valor

        for r in rateios:
            mid = r["membro_id"]
            if mid in saldos:
                saldos[mid] -= valor * (int(float(r["peso"])) / total_pesos)

    devedores = [[mid, -s] for mid, s in saldos.items() if s < -0.01]
    credores = [[mid, s] for mid, s in saldos.items() if s > 0.01]
    devedores.sort(key=lambda x: x[1], reverse=True)
    credores.sort(key=lambda x: x[1], reverse=True)

    transacoes = []
    i, j = 0, 0
    while i < len(devedores) and j < len(credores):
        dev_id, dev_val = devedores[i]
        cred_id, cred_val = credores[j]
        acerto = round(min(dev_val, cred_val), 2)
        if acerto >= 0.01:
            transacoes.append((dev_id, cred_id, acerto))
        devedores[i][1] -= acerto
        credores[j][1] -= acerto
        if devedores[i][1] < 0.01:
            i += 1
        if credores[j][1] < 0.01:
            j += 1

    transacoes.sort(key=lambda x: x[2])
    saldos_ordenados = sorted(saldos.items(), key=lambda x: x[1])

    return saldos_ordenados, transacoes


# --- Telas ---

def tela_autenticacao(sb: Client):
    st.title("🪙 Split Coin")
    st.caption("Divisão simples e justa de despesas em grupo.")

    aba_login, aba_cadastro, aba_recuperar = st.tabs(
        ["Entrar", "Criar Conta", "Esqueci a Senha (teste)"]
    )

    with aba_login:
        with st.form("form_login"):
            email = st.text_input("E-mail")
            senha = st.text_input("Senha", type="password")
            if st.form_submit_button("Entrar", use_container_width=True, type="primary"):
                try:
                    res = sb.auth.sign_in_with_password({"email": email.strip(), "password": senha})
                    st.session_state.sb_session = {
                        "access_token": res.session.access_token,
                        "refresh_token": res.session.refresh_token,
                    }
                    st.session_state.sb_user = {
                        "id": res.user.id,
                        "email": res.user.email,
                        "nome": (res.user.user_metadata or {}).get("nome")
                        or res.user.email.split("@")[0],
                    }
                    st.session_state.cookie_action = {
                        "op": "set",
                        "token": res.session.refresh_token,
                    }
                    st.rerun()
                except Exception as e:
                    msg = str(e)
                    if "banned" in msg.lower():
                        st.error("Este acesso foi bloqueado pelo proprietário do sistema.")
                    else:
                        st.error(f"Não foi possível entrar: {msg}")

    with aba_cadastro:
        with st.form("form_cadastro"):
            nome = st.text_input("Seu nome ou apelido", placeholder="Ex: João")
            email = st.text_input("E-mail")
            senha = st.text_input("Crie uma senha (mínimo 6 caracteres)", type="password")
            if st.form_submit_button("Criar minha conta", use_container_width=True, type="primary"):
                if not nome.strip():
                    st.error("Informe seu nome.")
                else:
                    try:
                        res = sb.auth.sign_up(
                            {
                                "email": email.strip(),
                                "password": senha,
                                "options": {"data": {"nome": nome.strip()}},
                            }
                        )
                        if res.session:
                            st.session_state.sb_session = {
                                "access_token": res.session.access_token,
                                "refresh_token": res.session.refresh_token,
                            }
                            st.session_state.sb_user = {
                                "id": res.user.id,
                                "email": res.user.email,
                                "nome": nome.strip(),
                            }
                            st.session_state.cookie_action = {
                                "op": "set",
                                "token": res.session.refresh_token,
                            }
                            st.rerun()
                        else:
                            st.success(
                                "Conta criada! Verifique seu e-mail e aguarde a aprovação "
                                "do proprietário para acessar."
                            )
                    except Exception as e:
                        st.error(f"Erro ao criar conta: {e}")

    with aba_recuperar:
        st.caption("Função em fase de testes. Caso precise redefinir sua senha, solicite ao proprietário do sistema.")
        with st.form("form_recuperar"):
            email_rec = st.text_input("E-mail cadastrado")
            st.form_submit_button(
                "Enviar link de redefinição",
                use_container_width=True,
                disabled=True,
            )
def render_painel_master(sb: Client, user: dict):
    st.subheader("⚡ Painel Master do Proprietário")
    st.caption("Aprovação de cadastros, controle global de usuários, senhas e grupos.")

    usuarios_all = sb.rpc("admin_listar_usuarios").execute().data or []
    grupos_all = sb.rpc("admin_listar_todos_grupos").execute().data or []

    pendentes_count = sum(1 for u in usuarios_all if not u.get("aprovado") and u.get("ativo"))
    ativos_count = sum(1 for u in usuarios_all if u.get("ativo") and u.get("aprovado"))
    bloqueados_count = sum(1 for u in usuarios_all if not u.get("ativo"))

    c1, c2, c3 = st.columns(3)
    c1.metric("Pendentes", pendentes_count)
    c2.metric("Aprovados", ativos_count)
    c3.metric("Bloqueados", bloqueados_count)

    sub_usuarios, sub_grupos = st.tabs(["👥 Gerenciar Usuários", "🗂️ Todos os Grupos"])

    with sub_usuarios:
        filtro = st.text_input(
            "Filtrar usuário",
            placeholder="Digite nome ou e-mail...",
            key="filtro_master_usuarios",
        ).strip().lower()

        usuarios_filtrados = [
            u for u in usuarios_all
            if not filtro or filtro in u["nome"].lower() or filtro in u["email"].lower()
        ]

        mapa_todos_grupos = {g["id"]: g["nome"] for g in grupos_all}

        for u in usuarios_filtrados:
            is_proprio = u["id"] == user["id"]
            aprovado = bool(u.get("aprovado"))
            ativo = bool(u.get("ativo"))

            badges = []
            if u.get("is_superadmin"):
                badges.append("⚡ Proprietário")
            if not ativo:
                badges.append("🔴 Bloqueado")
            elif not aprovado:
                badges.append("⏳ Aguardando aprovação")
            else:
                badges.append("🟢 Aprovado")

            with st.container(border=True):
                st.markdown(f"**{u['nome']}** — `{u['email']}`")
                st.caption(
                    f"{' • '.join(badges)} | Cadastro: {formatar_data_br(u.get('criado_em'))} "
                    f"| Último acesso: {formatar_data_br(u.get('ultimo_login'))}"
                )

                # Botão rápido de aprovação direto no card se estiver pendente
                if not is_proprio and not aprovado and ativo:
                    if st.button(
                        "✅ Aprovar cadastro agora",
                        key=f"adm_aprov_fast_{u['id']}",
                        type="primary",
                        use_container_width=True,
                    ):
                        sb.rpc(
                            "admin_aprovar_usuario",
                            {"p_user_id": u["id"], "p_aprovado": True},
                        ).execute()
                        st.rerun()

                grupos_u = u.get("grupos") or []
                if grupos_u:
                    st.markdown("**Grupos que participa:**")
                    for gu in grupos_u:
                        papel_g = "Gestor" if gu.get("is_gestor") else "Membro"
                        col_g1, col_g2 = st.columns([3, 1])
                        col_g1.write(f"- {gu['grupo_nome']} *({papel_g})*")
                        if col_g2.button("Remover", key=f"adm_rem_g_{gu['membro_id']}"):
                            try:
                                sb.rpc(
                                    "admin_remover_usuario_grupo",
                                    {"p_membro_id": gu["membro_id"]},
                                ).execute()
                                st.rerun()
                            except Exception:
                                st.error("Usuário possui despesas pagas neste grupo.")
                else:
                    st.caption("Não participa de nenhum grupo.")

                with st.expander("Gerenciar aprovação, bloqueio, senha e grupos"):
                    if not is_proprio:
                        if aprovado:
                            if st.button(
                                "⏳ Revogar aprovação (colocar em espera)",
                                key=f"adm_rev_aprov_{u['id']}",
                                use_container_width=True,
                            ):
                                sb.rpc(
                                    "admin_aprovar_usuario",
                                    {"p_user_id": u["id"], "p_aprovado": False},
                                ).execute()
                                st.rerun()
                        else:
                            if st.button(
                                "✅ Aprovar cadastro",
                                key=f"adm_aprov_{u['id']}",
                                type="primary",
                                use_container_width=True,
                            ):
                                sb.rpc(
                                    "admin_aprovar_usuario",
                                    {"p_user_id": u["id"], "p_aprovado": True},
                                ).execute()
                                st.rerun()

                        if ativo:
                            if st.button(
                                "🚫 Bloquear acesso",
                                key=f"adm_blk_{u['id']}",
                                use_container_width=True,
                            ):
                                sb.rpc(
                                    "admin_alterar_status_usuario",
                                    {"p_user_id": u["id"], "p_ativo": False},
                                ).execute()
                                st.rerun()
                        else:
                            if st.button(
                                "🔓 Desbloquear acesso",
                                key=f"adm_unblk_{u['id']}",
                                use_container_width=True,
                            ):
                                sb.rpc(
                                    "admin_alterar_status_usuario",
                                    {"p_user_id": u["id"], "p_ativo": True},
                                ).execute()
                                st.rerun()

                    with st.form(f"adm_form_senha_{u['id']}", clear_on_submit=True):
                        nova_senha = st.text_input(
                            "Redefinir senha (mínimo 6 caracteres)",
                            type="password",
                        )
                        if st.form_submit_button("Salvar nova senha", use_container_width=True):
                            if len(nova_senha.strip()) < 6:
                                st.error("A senha deve ter pelo menos 6 caracteres.")
                            else:
                                sb.rpc(
                                    "admin_resetar_senha_usuario",
                                    {"p_user_id": u["id"], "p_nova_senha": nova_senha.strip()},
                                ).execute()
                                st.success(f"Senha de {u['nome']} redefinida com sucesso!")

                    with st.form(f"adm_form_nome_{u['id']}"):
                        novo_nome_u = st.text_input("Nome do usuário", value=u["nome"])
                        if st.form_submit_button("Atualizar nome", use_container_width=True):
                            if novo_nome_u.strip():
                                sb.rpc(
                                    "admin_editar_nome_usuario",
                                    {"p_user_id": u["id"], "p_novo_nome": novo_nome_u.strip()},
                                ).execute()
                                if is_proprio:
                                    st.session_state.sb_user["nome"] = novo_nome_u.strip()
                                st.rerun()

                    if mapa_todos_grupos:
                        with st.form(f"adm_form_add_g_{u['id']}"):
                            gid_esc = st.selectbox(
                                "Adicionar ou promover em grupo",
                                options=list(mapa_todos_grupos.keys()),
                                format_func=lambda gid: mapa_todos_grupos[gid],
                            )
                            como_gestor = st.checkbox("Definir como Gestor do grupo")
                            if st.form_submit_button("Vincular ao grupo", use_container_width=True):
                                sb.rpc(
                                    "admin_vincular_usuario_grupo",
                                    {
                                        "p_user_id": u["id"],
                                        "p_grupo_id": gid_esc,
                                        "p_is_gestor": como_gestor,
                                    },
                                ).execute()
                                st.rerun()

                    if not is_proprio:
                        st.divider()
                        conf_del_u = st.checkbox(
                            "Confirmar exclusão permanente desta conta",
                            key=f"adm_chk_del_{u['id']}",
                        )
                        if st.button(
                            "🗑️ Excluir usuário",
                            key=f"adm_del_u_{u['id']}",
                            disabled=not conf_del_u,
                            use_container_width=True,
                        ):
                            sb.rpc("admin_excluir_usuario", {"p_user_id": u["id"]}).execute()
                            st.rerun()

    with sub_grupos:
        if not grupos_all:
            st.info("Nenhum grupo criado no sistema ainda.")
        else:
            for g in grupos_all:
                with st.container(border=True):
                    cod = g.get("codigo_convite") or "Não gerado"
                    st.markdown(f"**{g['nome']}** — Código: `{cod}`")
                    st.caption(
                        f"{g['total_membros']} participante(s) • {g['total_despesas']} despesa(s) "
                        f"• Criado em {formatar_data_br(g.get('criado_em'))}"
                    )
                    col_bg1, col_bg2 = st.columns(2)
                    if col_bg1.button(
                        "Entrar como Gestor",
                        key=f"adm_join_g_{g['id']}",
                        use_container_width=True,
                    ):
                        sb.rpc(
                            "admin_vincular_usuario_grupo",
                            {
                                "p_user_id": user["id"],
                                "p_grupo_id": g["id"],
                                "p_is_gestor": True,
                            },
                        ).execute()
                        st.session_state.grupo_ativo_id = g["id"]
                        st.rerun()

                    if col_bg2.button(
                        "🗑️ Excluir grupo",
                        key=f"adm_del_g_{g['id']}",
                        use_container_width=True,
                    ):
                        sb.rpc("admin_excluir_grupo", {"p_grupo_id": g["id"]}).execute()
                        st.rerun()


def app_principal(sb: Client):
    user = st.session_state.sb_user

    status_acesso = sb.rpc("meu_status_acesso").execute().data or {}
    if not status_acesso.get("ativo", True):
        try:
            sb.auth.sign_out()
        except Exception:
            pass
        st.session_state.clear()
        st.session_state.cookie_action = {"op": "clear"}
        st.error("Seu acesso foi bloqueado pelo proprietário do sistema.")
        st.stop()

    sou_superadmin = bool(status_acesso.get("superadmin", False))
    cadastro_aprovado = bool(status_acesso.get("aprovado", False)) or sou_superadmin

    # Tela de espera caso o cadastro ainda não tenha sido aprovado pelo proprietário
    if not cadastro_aprovado:
        st.title("🪙 Split Coin")
        with st.container(border=True):
            st.subheader("⏳ Cadastro aguardando aprovação")
            st.write(
                f"Olá, **{user['nome']}** (`{user['email']}`)! Sua conta foi criada com sucesso, "
                "mas o acesso ao Split Coin está restrito durante esta fase e depende da "
                "liberação do proprietário do sistema."
            )
            st.caption("Assim que seu cadastro for aprovado, toque em 'Verificar aprovação' abaixo.")
            c_esp1, c_esp2 = st.columns(2)
            if c_esp1.button("🔄 Verificar aprovação", type="primary", use_container_width=True):
                st.rerun()
            if c_esp2.button("Sair", use_container_width=True):
                try:
                    sb.auth.sign_out()
                except Exception:
                    pass
                st.session_state.clear()
                st.session_state.cookie_action = {"op": "clear"}
                st.rerun()
        return

    # Garantir e carregar perfil (Emoji padrão: 🙂)
    perfil_res = sb.table("perfis").select("*").eq("user_id", user["id"]).execute().data
    if perfil_res:
        meu_emoji = perfil_res[0].get("avatar_emoji") or "🙂"
    else:
        meu_emoji = "🙂"
        sb.table("perfis").insert(
            {"user_id": user["id"], "nome": user["nome"], "avatar_emoji": meu_emoji}
        ).execute()

    col_titulo, col_acoes = st.columns([3, 2])
    with col_titulo:
        st.title("🪙 Split Coin")
        selo_master = " • ⚡ **Proprietário**" if sou_superadmin else ""
        st.caption(f"{meu_emoji} **{user['nome']}**{selo_master}")

    with col_acoes:
        st.write("")
        b1, b2 = st.columns(2)
        if b1.button("🔄 Recarregar", use_container_width=True, help="Atualizar dados"):
            st.rerun()
        if b2.button("Sair", use_container_width=True):
            try:
                sb.auth.sign_out()
            except Exception:
                pass
            st.session_state.clear()
            st.session_state.cookie_action = {"op": "clear"}
            st.rerun()

    with st.expander(f"{meu_emoji} Alterar meu emoji de perfil"):
        with st.form("form_emoji_perfil"):
            emoji_digitado = st.text_input(
                "Emoji de perfil (padrão: 🙂)",
                value=meu_emoji,
                help="Digite ou cole apenas 1 emoji do teclado.",
            )
            if st.form_submit_button("Salvar emoji de perfil", use_container_width=True):
                emoji_ok = validar_apenas_emoji(emoji_digitado)
                if not emoji_ok:
                    st.error("Insira apenas 1 emoji válido (sem letras ou números).")
                else:
                    sb.table("perfis").update({"avatar_emoji": emoji_ok}).eq("user_id", user["id"]).execute()
                    st.rerun()

    grupos_res = sb.table("grupos").select("*").order("criado_em", desc=True).execute()
    grupos = grupos_res.data or []

    with st.expander("📁 Criar novo grupo ou entrar com código", expanded=(len(grupos) == 0)):
        c1, c2 = st.tabs(["Criar Grupo", "Entrar com Código"])
        with c1:
            with st.form("form_novo_grupo", clear_on_submit=True):
                nome_grupo = st.text_input("Nome do grupo", placeholder="Ex: Viagem Minas Gerais")
                if st.form_submit_button("Criar Grupo", use_container_width=True, type="primary"):
                    if nome_grupo.strip():
                        novo_id = sb.rpc(
                            "criar_grupo",
                            {"p_nome": nome_grupo.strip(), "p_meu_nome": user["nome"]},
                        ).execute()
                        st.session_state.grupo_ativo_id = novo_id.data
                        st.rerun()
        with c2:
            with st.form("form_entrar_grupo", clear_on_submit=True):
                codigo = st.text_input("Código de 6 caracteres", placeholder="Ex: A1B2C3")
                if st.form_submit_button("Entrar no Grupo", use_container_width=True):
                    try:
                        gid = sb.rpc(
                            "entrar_grupo",
                            {"p_codigo": codigo.strip(), "p_meu_nome": user["nome"]},
                        ).execute()
                        st.session_state.grupo_ativo_id = gid.data
                        st.rerun()
                    except Exception as e:
                        st.error(f"Não foi possível entrar: {e}")

    if not grupos:
        st.info("Crie seu primeiro grupo acima ou peça o código de convite ao gestor de um grupo.")
        if sou_superadmin:
            st.divider()
            render_painel_master(sb, user)
        return

    mapa_grupos = {g["id"]: g for g in grupos}
    ids_grupos = list(mapa_grupos.keys())
    default_idx = 0
    if st.session_state.get("grupo_ativo_id") in ids_grupos:
        default_idx = ids_grupos.index(st.session_state.grupo_ativo_id)

    grupo_id = st.selectbox(
        "Grupo ativo",
        options=ids_grupos,
        index=default_idx,
        format_func=lambda gid: f"{mapa_grupos[gid].get('icone') or '🪙'} {mapa_grupos[gid]['nome']}",
    )
    st.session_state.grupo_ativo_id = grupo_id
    grupo_atual = mapa_grupos[grupo_id]
    icone_grupo = grupo_atual.get("icone") or "🪙"

    membros = (
        sb.table("grupo_membros")
        .select("*")
        .eq("grupo_id", grupo_id)
        .order("criado_em")
        .execute()
        .data
        or []
    )

    user_ids_membros = [m["user_id"] for m in membros if m.get("user_id")]
    emojis_por_user = {}
    if user_ids_membros:
        perfis_membros = (
            sb.table("perfis")
            .select("user_id, avatar_emoji")
            .in_("user_id", user_ids_membros)
            .execute()
            .data
            or []
        )
        emojis_por_user = {p["user_id"]: (p.get("avatar_emoji") or "🙂") for p in perfis_membros}

    mapa_membros = {m["id"]: m["nome"] for m in membros}
    mapa_membros_emoji = {
        m["id"]: f"{emojis_por_user.get(m['user_id'], '🙂') if m.get('user_id') else '🙂'} {m['nome']}"
        for m in membros
    }

    meu_membro = next((m for m in membros if m.get("user_id") == user["id"]), None)
    sou_gestor = bool(meu_membro and meu_membro.get("is_gestor"))
    total_gestores = sum(1 for m in membros if m.get("is_gestor"))

    codigo_atual = grupo_atual.get("codigo_convite")
    if codigo_atual:
        st.caption("Código de convite do grupo (toque no ícone à direita para copiar):")
        st.code(codigo_atual, language=None)
    elif sou_gestor:
        if st.button("🎟️ Gerar código de convite para este grupo", use_container_width=True):
            sb.rpc("gerar_codigo_convite", {"p_grupo_id": grupo_id}).execute()
            st.rerun()

    despesas = (
        sb.table("despesas")
        .select("*, despesa_rateios(membro_id, peso)")
        .eq("grupo_id", grupo_id)
        .order("data_despesa", desc=True)
        .order("criado_em", desc=True)
        .execute()
        .data
        or []
    )

    saldos_ordenados, transacoes = calcular_acerto(membros, despesas)

    nomes_abas = ["📊 Resumo", "➕ Novo Registro", "🧾 Extrato", "⚙️ Grupo"]
    if sou_superadmin:
        nomes_abas.append("⚡ Master")

    abas = st.tabs(nomes_abas)

    # --- ABA 1: RESUMO ---
    with abas[0]:
        st.subheader(f"{icone_grupo} Resumo — {grupo_atual['nome']}")

        despesas_reais = [d for d in despesas if d.get("tipo", "despesa") == "despesa"]
        gastos_totais = sum(float(d["valor"]) for d in despesas_reais)
        qtd_membros = len(membros)
        gasto_medio = (gastos_totais / qtd_membros) if qtd_membros > 0 else 0.0

        m1, m2 = st.columns(2)
        m1.metric("Gastos totais", formatar_moeda(gastos_totais))
        m2.metric(
            "Gasto médio",
            formatar_moeda(gasto_medio),
            help=f"Média simples dividida pelos {qtd_membros} participantes atuais.",
        )

        st.divider()
        st.subheader("Acerto simplificado")
        if not transacoes:
            st.success("Tudo quitado! Ninguém deve nada.")
        else:
            for idx_t, (dev_id, cred_id, quantia) in enumerate(transacoes, start=1):
                with st.container(border=True):
                    st.markdown(
                        f"**{idx_t}.** {mapa_membros_emoji[dev_id]} paga "
                        f"**{formatar_moeda(quantia)}** para {mapa_membros_emoji[cred_id]}"
                    )

        st.subheader("Saldo individual")
        for mid, saldo in saldos_ordenados:
            nome_exib = mapa_membros_emoji[mid]
            if saldo < -0.01:
                st.write(f"🔴 {nome_exib}: **deve {formatar_moeda(-saldo)}**")
            elif saldo > 0.01:
                st.write(f"🟢 {nome_exib}: **recebe {formatar_moeda(saldo)}**")
            else:
                st.write(f"⚪ {nome_exib}: zerado")

    # --- ABA 2: NOVO REGISTRO ---
    with abas[1]:
        opcoes_pagador = [m["id"] for m in membros]
        idx_pagador_padrao = (
            opcoes_pagador.index(meu_membro["id"])
            if meu_membro and meu_membro["id"] in opcoes_pagador
            else 0
        )

        tipo_registro = st.radio(
            "Tipo de lançamento",
            options=["🧾 Despesa", "💸 Transferência (Sangria)"],
            horizontal=True,
        )

        if tipo_registro == "🧾 Despesa":
            with st.form("form_despesa", clear_on_submit=True):
                descricao = st.text_input("Descrição", placeholder="Ex: Almoço Ouro Preto")
                valor_str = st.text_input("Valor total (R$)", placeholder="0,00")
                data_gasto = st.date_input("Data", value=date.today(), format="DD/MM/YYYY")
                pagador_id = st.selectbox(
                    "Quem pagou?",
                    options=opcoes_pagador,
                    index=idx_pagador_padrao,
                    format_func=lambda mid: mapa_membros_emoji[mid],
                )

                st.markdown("**Pesos da divisão** *(1 = média padrão, 0 = não participa)*")
                pesos_form = {}
                for m in membros:
                    pesos_form[m["id"]] = st.number_input(
                        f"Peso — {mapa_membros_emoji[m['id']]}",
                        min_value=0,
                        max_value=50,
                        value=1,
                        step=1,
                        key=f"peso_{grupo_id}_{m['id']}",
                    )

                if st.form_submit_button("Salvar Despesa", use_container_width=True, type="primary"):
                    valor_num = parse_valor_br(valor_str)
                    if not descricao.strip():
                        st.error("Informe uma descrição para a despesa.")
                    elif valor_num is None:
                        st.error("Informe um valor válido maior que zero (ex: 125,50).")
                    elif sum(pesos_form.values()) <= 0:
                        st.error("Pelo menos um participante precisa ter peso maior que zero.")
                    else:
                        nova_desp = (
                            sb.table("despesas")
                            .insert(
                                {
                                    "grupo_id": grupo_id,
                                    "pago_por_membro_id": pagador_id,
                                    "descricao": descricao.strip(),
                                    "valor": valor_num,
                                    "data_despesa": data_gasto.isoformat(),
                                    "tipo": "despesa",
                                }
                            )
                            .execute()
                            .data[0]
                        )
                        rateios_payload = [
                            {
                                "despesa_id": nova_desp["id"],
                                "membro_id": mid,
                                "peso": int(p),
                            }
                            for mid, p in pesos_form.items()
                        ]
                        sb.table("despesa_rateios").insert(rateios_payload).execute()
                        st.success("Despesa registrada!")
                        st.rerun()
        else:
            st.caption(
                "Registra uma transferência direta entre dois participantes (sangria/adiantamento), "
                "gerando crédito para quem pagou e débito para quem recebeu."
            )
            if len(membros) < 2:
                st.warning("Adicione pelo menos 2 participantes ao grupo para registrar uma transferência.")
            else:
                with st.form("form_transferencia", clear_on_submit=True):
                    pagador_tr_id = st.selectbox(
                        "Quem pagou (Origem)",
                        options=opcoes_pagador,
                        index=idx_pagador_padrao,
                        format_func=lambda mid: mapa_membros_emoji[mid],
                    )
                    idx_rec_padrao = 1 if idx_pagador_padrao == 0 else 0
                    recebedor_tr_id = st.selectbox(
                        "Quem recebeu (Destino)",
                        options=opcoes_pagador,
                        index=idx_rec_padrao,
                        format_func=lambda mid: mapa_membros_emoji[mid],
                    )
                    valor_tr_str = st.text_input("Valor transferido (R$)", placeholder="0,00")
                    data_tr = st.date_input("Data", value=date.today(), format="DD/MM/YYYY")
                    desc_tr = st.text_input(
                        "Observação (opcional)",
                        placeholder="Ex: Sangria / Adiantamento Pix",
                    )

                    if st.form_submit_button(
                        "Registrar Transferência",
                        use_container_width=True,
                        type="primary",
                    ):
                        valor_tr = parse_valor_br(valor_tr_str)
                        if pagador_tr_id == recebedor_tr_id:
                            st.error("O pagador e o recebedor precisam ser pessoas diferentes.")
                        elif valor_tr is None:
                            st.error("Informe um valor válido maior que zero (ex: 50,00).")
                        else:
                            texto_desc = desc_tr.strip() if desc_tr.strip() else "Transferência"
                            nova_tr = (
                                sb.table("despesas")
                                .insert(
                                    {
                                        "grupo_id": grupo_id,
                                        "pago_por_membro_id": pagador_tr_id,
                                        "descricao": texto_desc,
                                        "valor": valor_tr,
                                        "data_despesa": data_tr.isoformat(),
                                        "tipo": "transferencia",
                                    }
                                )
                                .execute()
                                .data[0]
                            )
                            sb.table("despesa_rateios").insert(
                                {
                                    "despesa_id": nova_tr["id"],
                                    "membro_id": recebedor_tr_id,
                                    "peso": 1,
                                }
                            ).execute()
                            st.success("Transferência registrada!")
                            st.rerun()

    # --- ABA 3: EXTRATO ---
    with abas[2]:
        if sou_gestor:
            lotes_ativos = {}
            for d in despesas:
                if (
                    d.get("tipo") == "quitacao"
                    and not d.get("quitacao_revertida", False)
                    and d.get("lote_quitacao_id")
                ):
                    lid = d["lote_quitacao_id"]
                    if lid not in lotes_ativos:
                        lotes_ativos[lid] = {
                            "lote_id": lid,
                            "data": d["data_despesa"],
                            "total": 0.0,
                            "qtd": 0,
                        }
                    lotes_ativos[lid]["total"] += float(d["valor"])
                    lotes_ativos[lid]["qtd"] += 1

            if transacoes:
                with st.expander("🤝 Quitar contas (zerar saldos em aberto)"):
                    st.write(
                        "Serão geradas automaticamente as transferências abaixo para liquidar "
                        "todos os saldos pendentes do grupo:"
                    )
                    for idx_q, (dev_id, cred_id, quantia) in enumerate(transacoes, start=1):
                        st.write(
                            f"**{idx_q}.** {mapa_membros_emoji[dev_id]} → "
                            f"{mapa_membros_emoji[cred_id]}: **{formatar_moeda(quantia)}**"
                        )

                    conf_quitar = st.checkbox(
                        "Confirmar geração das transferências de quitação",
                        key=f"chk_quitar_{grupo_id}",
                    )
                    if st.button(
                        "✅ Confirmar quitação",
                        type="primary",
                        disabled=not conf_quitar,
                        use_container_width=True,
                    ):
                        lote_id = str(uuid.uuid4())
                        hoje_iso = date.today().isoformat()
                        for dev_id, cred_id, quantia in transacoes:
                            nova_q = (
                                sb.table("despesas")
                                .insert(
                                    {
                                        "grupo_id": grupo_id,
                                        "pago_por_membro_id": dev_id,
                                        "descricao": "Quitação de conta",
                                        "valor": float(quantia),
                                        "data_despesa": hoje_iso,
                                        "tipo": "quitacao",
                                        "lote_quitacao_id": lote_id,
                                        "quitacao_revertida": False,
                                    }
                                )
                                .execute()
                                .data[0]
                            )
                            sb.table("despesa_rateios").insert(
                                {
                                    "despesa_id": nova_q["id"],
                                    "membro_id": cred_id,
                                    "peso": 1,
                                }
                            ).execute()
                        st.success("Contas quitadas! Todos os saldos foram zerados.")
                        st.rerun()

            if lotes_ativos:
                ultimo_lote = list(lotes_ativos.values())[0]
                with st.expander("↩️ Reverter última quitação"):
                    st.info(
                        f"Quitação ativa de **{formatar_data_br(ultimo_lote['data'])}** "
                        f"({ultimo_lote['qtd']} lançamento(s), total de {formatar_moeda(ultimo_lote['total'])}). "
                        "A reversão não apaga os registros: ela cria lançamentos negativos correspondentes no extrato."
                    )
                    conf_reverter = st.checkbox(
                        "Confirmar reversão desta quitação",
                        key=f"chk_rev_{ultimo_lote['lote_id']}",
                    )
                    if st.button(
                        "↩️ Reverter conciliação",
                        disabled=not conf_reverter,
                        use_container_width=True,
                    ):
                        try:
                            sb.rpc(
                                "reverter_quitacao_grupo",
                                {
                                    "p_grupo_id": grupo_id,
                                    "p_lote_id": ultimo_lote["lote_id"],
                                },
                            ).execute()
                            st.success("Quitação revertida com lançamentos negativos!")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Não foi possível reverter: {e}")

        if not despesas:
            st.info("Nenhum lançamento registrado neste grupo ainda.")
        else:
            csv_bytes = gerar_csv_extrato(membros, despesas)
            timestamp_csv = datetime.now().strftime("%Y%m%d_%H%M%S")
            slug_grupo = grupo_atual["nome"].strip().lower().replace(" ", "_")
            nome_arquivo_csv = f"extrato_{slug_grupo}_{timestamp_csv}.csv"

            st.download_button(
                label="📥 Baixar extrato em CSV",
                data=csv_bytes,
                file_name=nome_arquivo_csv,
                mime="text/csv",
                use_container_width=True,
            )

            for d in despesas:
                val = float(d["valor"])
                tipo = d.get("tipo", "despesa")
                pagador_exib = mapa_membros_emoji.get(d["pago_por_membro_id"], "Desconhecido")
                data_br = formatar_data_br(d["data_despesa"])
                rateios = d.get("despesa_rateios", [])

                with st.container(border=True):
                    if tipo == "despesa":
                        st.markdown(f"**{d['descricao']}** — {formatar_moeda(val)}")
                        st.caption(f"Pago por **{pagador_exib}** em {data_br}")
                    else:
                        rec_id = next(
                            (r["membro_id"] for r in rateios if int(float(r["peso"])) > 0),
                            None,
                        )
                        rec_exib = mapa_membros_emoji.get(rec_id, "Desconhecido")
                        badge_tipo = {
                            "transferencia": "💸 Transferência",
                            "quitacao": "🤝 Quitação",
                            "estorno_quitacao": "↩️ Estorno",
                        }.get(tipo, "💸 Transferência")
                        st.markdown(f"{badge_tipo}: **{d['descricao']}** — {formatar_moeda(val)}")
                        st.caption(f"De **{pagador_exib}** para **{rec_exib}** em {data_br}")

                    with st.expander("Detalhes e ações"):
                        if tipo == "despesa":
                            total_p = sum(int(float(r["peso"])) for r in rateios)
                            for r in rateios:
                                p = int(float(r["peso"]))
                                if p > 0 and total_p > 0:
                                    cota = val * (p / total_p)
                                    nome_m = mapa_membros_emoji.get(r["membro_id"], "Membro")
                                    st.write(f"- {nome_m} (peso {p}): **{formatar_moeda(cota)}**")
                        elif val < 0:
                            st.write(f"- Estorno pagador ({pagador_exib}): **{formatar_moeda(val)}**")
                            st.write(f"- Estorno recebedor ({rec_exib}): **+{formatar_moeda(abs(val))}**")
                        else:
                            st.write(f"- Origem (crédito): {pagador_exib} (**+{formatar_moeda(val)}**)")
                            st.write(f"- Destino (débito): {rec_exib} (**-{formatar_moeda(val)}**)")

                        if tipo in ("quitacao", "estorno_quitacao"):
                            st.caption("Registros de quitação são auditáveis e controlados pela reversão.")
                        else:
                            if st.button("Excluir lançamento", key=f"del_{d['id']}"):
                                sb.table("despesas").delete().eq("id", d["id"]).execute()
                                st.rerun()

    # --- ABA 4: GESTÃO DO GRUPO E PARTICIPANTES ---
    with abas[3]:
        if sou_gestor:
            st.info("Você é **Gestor** deste grupo e pode editar suas informações e participantes.")

            st.subheader("Informações e ícone do grupo")
            with st.form("form_editar_grupo"):
                novo_nome_grupo = st.text_input("Nome do grupo", value=grupo_atual["nome"])
                novo_icone_input = st.text_input(
                    "Ícone do grupo (apenas 1 emoji — padrão: 🪙)",
                    value=icone_grupo,
                    help="Digite ou cole apenas 1 emoji do teclado.",
                )
                if st.form_submit_button("Salvar informações do grupo", use_container_width=True, type="primary"):
                    emoji_grupo_ok = validar_apenas_emoji(novo_icone_input)
                    if not novo_nome_grupo.strip():
                        st.error("O nome do grupo não pode ficar vazio.")
                    elif not emoji_grupo_ok:
                        st.error("O ícone do grupo deve conter apenas 1 emoji válido (sem letras ou números).")
                    else:
                        sb.table("grupos").update(
                            {"nome": novo_nome_grupo.strip(), "icone": emoji_grupo_ok}
                        ).eq("id", grupo_id).execute()
                        st.success("Grupo atualizado!")
                        st.rerun()

            st.markdown("**Convite de participantes**")
            label_btn_codigo = (
                "🔄 Gerar novo código de convite (invalida o anterior)"
                if codigo_atual
                else "🎟️ Gerar código de convite"
            )
            if st.button(label_btn_codigo, use_container_width=True):
                sb.rpc("gerar_codigo_convite", {"p_grupo_id": grupo_id}).execute()
                st.rerun()

            st.divider()

            membros_sem_conta = [m for m in membros if not m.get("user_id")]
            membros_com_conta = [m for m in membros if m.get("user_id")]

            if membros_sem_conta:
                st.subheader("Vincular participante sem conta")
                st.caption(
                    "Transfere 100% dos pagamentos e rateios passados de um participante "
                    "sem conta para um usuário cadastrado."
                )
                modo_vinc = st.tabs(["Já entrou no grupo", "Vincular por e-mail"])

                with modo_vinc[0]:
                    if not membros_com_conta:
                        st.info("Nenhum outro participante com conta vinculada neste grupo ainda.")
                    else:
                        with st.form("form_vincular_existente"):
                            origem_id = st.selectbox(
                                "Participante sem conta (histórico a transferir)",
                                options=[m["id"] for m in membros_sem_conta],
                                format_func=lambda mid: mapa_membros[mid],
                            )
                            destino_id = st.selectbox(
                                "Usuário com conta (quem vai assumir os registros)",
                                options=[m["id"] for m in membros_com_conta],
                                format_func=lambda mid: mapa_membros_emoji[mid],
                            )
                            if st.form_submit_button(
                                "Transferir registros e vincular",
                                use_container_width=True,
                                type="primary",
                            ):
                                try:
                                    sb.rpc(
                                        "vincular_membro_existente_grupo",
                                        {
                                            "p_grupo_id": grupo_id,
                                            "p_membro_sem_conta_id": origem_id,
                                            "p_membro_com_conta_id": destino_id,
                                        },
                                    ).execute()
                                    st.success("Histórico transferido e contas unificadas!")
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"Erro ao vincular: {e}")

                with modo_vinc[1]:
                    with st.form("form_vincular_email", clear_on_submit=True):
                        origem_email_id = st.selectbox(
                            "Participante sem conta",
                            options=[m["id"] for m in membros_sem_conta],
                            format_func=lambda mid: mapa_membros[mid],
                        )
                        email_destino = st.text_input(
                            "E-mail cadastrado do usuário no Split Coin",
                            placeholder="usuario@exemplo.com",
                        )
                        if st.form_submit_button(
                            "Vincular conta por e-mail",
                            use_container_width=True,
                            type="primary",
                        ):
                            if not email_destino.strip():
                                st.error("Informe o e-mail do usuário.")
                            else:
                                try:
                                    sb.rpc(
                                        "vincular_membro_por_email",
                                        {
                                            "p_grupo_id": grupo_id,
                                            "p_membro_sem_conta_id": origem_email_id,
                                            "p_email": email_destino.strip(),
                                        },
                                    ).execute()
                                    st.success("Participante vinculado à conta com sucesso!")
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"Não foi possível vincular: {e}")

                st.divider()

            st.subheader("Gerenciar participantes")
            with st.form("form_membro_avulso", clear_on_submit=True):
                novo_participante = st.text_input(
                    "Adicionar participante sem conta no app",
                    placeholder="Ex: Primo Lucas",
                )
                if st.form_submit_button("Adicionar participante", use_container_width=True):
                    if novo_participante.strip():
                        sb.table("grupo_membros").insert(
                            {
                                "grupo_id": grupo_id,
                                "nome": novo_participante.strip(),
                                "is_gestor": False,
                            }
                        ).execute()
                        st.rerun()

            for m in membros:
                papel = "👑 Gestor" if m.get("is_gestor") else "Membro"
                conta = "conta vinculada" if m.get("user_id") else "sem conta"
                with st.container(border=True):
                    st.markdown(f"**{mapa_membros_emoji[m['id']]}** — {papel} *({conta})*")
                    with st.expander("Editar ou remover"):
                        with st.form(f"form_edit_membro_{m['id']}"):
                            nome_edit = st.text_input("Nome exibido no grupo", value=m["nome"])
                            novo_status_gestor = m.get("is_gestor", False)

                            if m.get("user_id"):
                                desabilitar_check = m.get("is_gestor") and total_gestores <= 1
                                novo_status_gestor = st.checkbox(
                                    "Gestor do grupo",
                                    value=bool(m.get("is_gestor")),
                                    disabled=desabilitar_check,
                                    help="O grupo precisa ter pelo menos um gestor ativo.",
                                )

                            if st.form_submit_button("Salvar alterações", use_container_width=True):
                                if nome_edit.strip():
                                    sb.table("grupo_membros").update(
                                        {
                                            "nome": nome_edit.strip(),
                                            "is_gestor": bool(novo_status_gestor),
                                        }
                                    ).eq("id", m["id"]).execute()
                                    st.rerun()

                        pode_remover = not (m.get("is_gestor") and total_gestores <= 1)
                        if pode_remover:
                            if st.button("Remover do grupo", key=f"rem_membro_{m['id']}"):
                                try:
                                    sb.table("grupo_membros").delete().eq("id", m["id"]).execute()
                                    st.rerun()
                                except Exception:
                                    st.error(
                                        "Não é possível remover este participante porque existem despesas "
                                        "pagas por ele. Exclua essas despesas antes."
                                    )
                        else:
                            st.caption("Promova outro membro a gestor antes de remover este.")

            st.divider()

            with st.expander("🗑️ Excluir este grupo permanentemente"):
                confirmar_exclusao = st.checkbox(
                    "Entendo que todas as despesas e rateios deste grupo serão apagados."
                )
                if st.button(
                    "Excluir grupo",
                    type="primary",
                    disabled=not confirmar_exclusao,
                    use_container_width=True,
                ):
                    sb.table("grupos").delete().eq("id", grupo_id).execute()
                    st.session_state.pop("grupo_ativo_id", None)
                    st.rerun()

        else:
            st.subheader("Participantes do grupo")
            for m in membros:
                papel = "👑 Gestor" if m.get("is_gestor") else "Membro"
                st.write(f"- **{mapa_membros_emoji[m['id']]}** ({papel})")

            st.divider()
            if meu_membro and st.button("Sair deste grupo"):
                try:
                    sb.table("grupo_membros").delete().eq("id", meu_membro["id"]).execute()
                    st.session_state.pop("grupo_ativo_id", None)
                    st.rerun()
                except Exception:
                    st.error(
                        "Você possui despesas pagas registradas neste grupo e não pode sair "
                        "sem que elas sejam removidas."
                    )

    # --- ABA 5: PAINEL MASTER (APENAS PROPRIETÁRIO) ---
    if sou_superadmin:
        with abas[4]:
            render_painel_master(sb, user)


def main():
    sb = get_supabase()
    if "sb_user" not in st.session_state or not st.session_state.sb_user:
        tela_autenticacao(sb)
    else:
        app_principal(sb)
    sincronizar_cookie_sessao()


if __name__ == "__main__":
    main()