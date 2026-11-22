# [🪙 Split Coin](https://splitcoin.streamlit.app/)

Aplicação web responsiva para divisão justa e auditável de despesas em grupo, desenvolvida em **Python (Streamlit)** com backend relacional e autenticação via **Supabase (PostgreSQL)**.

---

## 1. Arquitetura e Stack Tecnológica

* **Frontend & Orquestração:** Streamlit (`app.py`), projetado com layout centralizado (`layout="centered"`) e navegação por abas para uso fluido em navegadores desktop e mobile.
* **Backend, Banco de Dados e Autenticação:** Supabase (PostgreSQL 15+ com *Row Level Security* ativo em todas as tabelas e funções `SECURITY DEFINER` para operações privilegiadas).
* **Persistência de Sessão:** Token de atualização (`refresh_token`) sincronizado em cookie de navegador (`splitcoin_rt`), evitando perda de sessão ao recarregar a página (`F5`).

---

## 2. Estrutura de Arquivos

```text
split_coin/
├── .streamlit/
│   └── secrets.toml      # Credenciais do Supabase (local apenas - ignorado pelo Git)
├── .gitignore           # Bloqueia envio de credenciais e ambiente virtual
├── app.py               # Aplicação principal (UI, regras de rateio, CSV e painéis)
├── README.md            # Documentação técnica e funcional do projeto
└── requirements.txt     # Dependências Python (streamlit, supabase)
```

---

## 3. Modelo de Dados (PostgreSQL / Supabase)

| Tabela | Descrição | Campos Principais |
| :--- | :--- | :--- |
| `perfis` | Perfil global do usuário e controle de aprovação | `user_id`, `nome`, `avatar_emoji` (padrão `🙂`), `aprovado` |
| `app_superadmins` | Lista blindada de proprietários do sistema | `user_id`, `criado_em` |
| `grupos` | Grupos temáticos de rateio | `id`, `nome`, `icone` (padrão `🪙`), `codigo_convite`, `criado_por` |
| `grupo_membros` | Vínculo entre grupos e participantes (com ou sem conta) | `id`, `grupo_id`, `user_id` (opcional), `nome`, `is_gestor` |
| `despesas` | Lançamentos financeiros do grupo | `id`, `grupo_id`, `pago_por_membro_id`, `descricao`, `valor`, `data_despesa`, `tipo`, `lote_quitacao_id`, `quitacao_revertida` |
| `despesa_rateios` | Pesos aplicados a cada participante por lançamento | `id`, `despesa_id`, `membro_id`, `peso` (inteiro $\ge 0$) |

---

## 4. Hierarquia de Perfis e Segurança

1. **Proprietário (`app_superadmins` — Aba `⚡ Master`):**
   * Aprova ou revoga novos cadastros antes que tenham acesso ao sistema.
   * Bloqueia/desbloqueia usuários, redefine senhas diretamente e exclui contas de teste.
   * Visualiza e gerencia qualquer grupo cadastrado no sistema.
2. **Gestor do Grupo (`is_gestor = true` — Aba `⚙️ Grupo`):**
   * Edita nome e ícone (emoji) do grupo.
   * Gera ou renova o código de convite de 6 caracteres.
   * Adiciona participantes sem conta, promove/rebaixa gestores e unifica o histórico de um participante sem conta com um usuário cadastrado.
   * Executa a quitação em lote dos saldos e a reversão contábil da última quitação.
3. **Membro Comum (`is_gestor = false`):**
   * Visualiza o resumo, lança despesas ou transferências, consulta o extrato e exporta relatórios em CSV.

---

## 5. Regras Financeiras e Contábeis

* **Rateio por Pesos Inteiros:** Cada lançamento de despesa distribui o valor proporcionalmente ao peso de cada integrante (`cota = valor * peso_individual / soma_pesos`). Peso `0` exclui o participante daquela despesa.
* **Transferências (Sangrias):** Lançamentos diretos de um pagador (`+valor`) para um recebedor (`-valor`), sem entrar no cálculo de *Gastos Totais* nem *Gasto Médio* do grupo.
* **Algoritmo de Acerto Simplificado:** Consolida os saldos líquidos individuais e emparelha maiores devedores com maiores credores para minimizar o número de transferências necessárias, exibindo-as ordenadas do menor para o maior valor.
* **Quitação Auditável e Estorno Negativo:** A quitação gera lançamentos do tipo `quitacao` zerando os saldos. Caso revertida, o sistema não apaga o histórico: cria lançamentos `estorno_quitacao` com valor negativo mantendo o mesmo pagador e recebedor.
* **Exportação CSV:** Gera arquivo UTF-8 com BOM (`;`), valores decimais com vírgula, datas em `dd/mm/aaaa` e timestamp no nome (`extrato_<grupo>_YYYYMMDD_HHMMSS.csv`).

---

## 6. Execução Local

```bash
# 1. Ativar ambiente virtual (Windows PowerShell)
.venv\Scripts\Activate.ps1   # ou venv\Scripts\Activate.ps1

# 2. Instalar dependências
pip install -r requirements.txt

# 3. Configurar .streamlit/secrets.toml com SUPABASE_URL e SUPABASE_KEY
# 4. Iniciar aplicação
streamlit run app.py
```
