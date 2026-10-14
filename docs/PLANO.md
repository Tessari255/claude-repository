# Plano: da Trama de hoje a uma plataforma no nível do Power Automate

Este arquivo é a especificação de trabalho. Cada item tem um código (A1, B3…) que os commits e os testes citam.
A versão anterior do plano (fases 0 e 1: CI, lint, refatoração, pool aquecido, laço em lote) está concluída e o histórico dela fica no git.

## Objetivo

Usar a Trama para desenvolver aplicações de forma ágil e fácil de manter. O Power Automate **automatiza processos**; quem constrói
aplicações na Power Platform é o Power Apps. Para a Trama servir de base a aplicações, ela precisa de duas coisas que o Power Automate
tem e ela não tinha: **ser chamada de fora** (webhook + resposta HTTP) e **esperar** (aprovação, atraso longo, evento externo).
O plano põe essas duas no centro.

**Não vamos copiar:** os 1.000+ conectores prontos (a resposta da Trama são conectores internos + conector personalizado declarativo),
fluxos de área de trabalho (RPA), mineração de processos e AI Builder.

## Ponto de partida

Pronto: gatilho manual com campos tipados; condição, Para cada, Repetir até, Escopo, Encerrar, variáveis; conteúdo dinâmico; "executar
após" e retentativas; verificador; painel de teste com dados de execução anterior; histórico com o fluxo da época; modelos;
exportar/importar; Python isolado em contêiner; pool aquecido (`TRAMA_POOL`) e Para cada em lote; CI, ruff, pyright e ESLint.

## Decisões (as perguntas abertas da análise, respondidas com a recomendação)

1. **Banco:** SQLite até a Fase E. `store.py` é a única camada que conhece o banco; trocar por PostgreSQL depois mexe só nela.
2. **Login:** contas locais (senha com `hashlib.scrypt`, sessão em cookie `HttpOnly` + `SameSite=Strict`). Login corporativo (Entra/OIDC) fica fora por ora.
3. **Rede:** o código Python do usuário continua **sem rede**. Os conectores são código nosso que roda no processo da API, como os
   conectores gerenciados do Power Automate, e só alcançam hosts de uma **lista aprovada** (por conexão e global). IPs privados,
   de loopback e link-local são recusados depois da resolução de DNS, salvo liberação explícita. Isto substitui a ideia de um "contêiner
   com rede para código do usuário": o mesmo resultado (integrações com credenciais) com bem menos superfície de ataque.
4. **Ramos paralelos:** fora. Complicam "executar após", o histórico e o "ver como código". Ficam para depois do plano.
5. **Opcionais da análise que ficam fora:** copiloto por IA, login corporativo e a imagem com pandas/numpy. Entram só se forem pedidos.

## Fases

### Fase A — Fechar a base de Python
- **A1. Expressões Python inline** nos campos (`len(itens) * 2`, `nome.strip().title()`): terceiro modo de campo ao lado de valor fixo e
  conteúdo dinâmico; validadas só por análise estática no processo da API; avaliadas **no contêiner**, todas as de um passo num único trabalho.
- **A2. Testar só este passo** com os dados de uma execução anterior.
- **A3. Ver como código:** o fluxo vira um script Python legível; o script, executado no contêiner, reproduz o resultado do motor.
- **A4. Autocompletar e lint** no editor de código (sem executar o código fora do contêiner).
- **A5. Validar pool aquecido e Para cada em lote** (suítes completas e medição).

*Pronto quando:* os exemplos têm script gerado que reproduz o resultado; um Para cada de 50 itens com Python roda em < 5 s.

### Fase B — Gatilhos e execução durável
- **B1. Motor durável:** a execução pode ficar **aguardando** e ser retomada, inclusive depois de reiniciar o servidor. Retomada por
  *replay*: o motor percorre o fluxo de novo e, para cada passo já concluído no histórico, usa as saídas gravadas em vez de executá-lo.
  Execuções que estavam *executando* quando o servidor caiu continuam virando falha (nada roda duas vezes sem querer).
- **B2. Ligar/desligar fluxo** e **gatilho Recorrência** (a cada N minutos/horas/dias/semanas, horário, dias da semana, fuso) com
  agendador no processo da API e "próximas execuções". Sem recuperar execuções perdidas enquanto o servidor estava desligado.
- **B3. Gatilho "Quando uma requisição HTTP é recebida"** (URL com token secreto por fluxo) + passo **Responder à requisição**
  (status, corpo, cabeçalhos permitidos). Quem chama recebe a resposta calculada pelo fluxo; sem Responder, recebe 202 com o id da execução.
- **B4. Esperas:** **Atraso** (segundos a dias), **Criar aprovação** (gera um link secreto e uma página Aprovar/Rejeitar com comentário)
  + **Aguardar aprovação** (o resultado vira conteúdo dinâmico), caixa de **Aprovações** pendentes no app.
- **B5. Executar fluxo filho** (entradas e saídas declaradas; o pai espera o filho, inclusive se o filho ficar aguardando).
- **B6. Operação do histórico:** retenção (`TRAMA_RETENCAO_DIAS`, padrão 30), filtros por estado, **reenviar execução**, eventos em tempo real
  (SSE) no lugar do polling (o polling fica como reserva).

*Pronto quando:* um fluxo agendado roda sozinho; um fluxo com Aguardar aprovação sobrevive a um reinício do servidor e termina quando alguém
aprova; um `curl` no webhook recebe a resposta calculada pelo fluxo.

### Fase C — Conectores
- **C1. Cofre de conexões:** credenciais criptografadas em repouso (chave em `TRAMA_CHAVE_COFRE`), nunca no fluxo exportado nem no histórico,
  mascaradas nos logs; o fluxo guarda só a *referência* à conexão.
- **C2. Política de rede:** lista global de hosts permitidos (equivalente às políticas DLP) + hosts por conexão; bloqueio de IPs privados após o DNS;
  tempo limite e teto de tamanho de resposta em toda chamada.
- **C3. Conectores internos:** Requisição HTTP (métodos, JSON, autenticação básica/bearer/chave de API), E-mail (SMTP com TLS),
  Banco SQL (SQLite e PostgreSQL; consultas parametrizadas), Arquivos numa pasta permitida (CSV, JSON, XLSX; sem sair da pasta).
- **C4. Conector personalizado declarativo** (como os do Power Automate): URL base, tipo de autenticação e operações (método, caminho,
  parâmetros, corpo) descritos num formulário/JSON; cada operação vira um passo; viaja na exportação sem segredos.

*Pronto quando:* um fluxo agendado lê um banco, calcula em Python e manda e-mail, e a senha nunca aparece em logs, histórico ou arquivo exportado.

### Fase D — Controle e dados
- **D1. Switch** (casos por valor + padrão).
- **D2. Condições de gatilho** e **controle de concorrência** (quantas execuções do mesmo fluxo ao mesmo tempo; as demais esperam na fila).
- **D3. Dados:** Analisar JSON (com esquema), Filtrar lista, Ordenar lista, Juntar/Dividir texto, Tabela CSV/HTML.
- **D4. Data e hora:** agora, formatar, somar/subtrair, converter fuso.
- **D5. "Executar após" apontando para qualquer passo anterior do mesmo nível** (fluxos antigos continuam significando "o anterior").

*Pronto quando:* cada passo novo tem exemplo na galeria e aparece corretamente no "ver como código".

### Fase E — Equipe e manutenção
- **E1. Contas e sessões** (administrador criado na primeira execução), papéis por fluxo: proprietário, editor, somente executar.
- **E2. Compartilhamento** de fluxos, blocos e conexões; execuções mostram quem disparou.
- **E3. Histórico de versões do fluxo**, com diferenças por passo e "restaurar".
- **E4. Ambientes e variáveis de ambiente** (o mesmo fluxo com valores e conexões de desenvolvimento ou de produção).
- **E5. Soluções:** exportar/importar um pacote com vários fluxos, blocos, referências de conexão e definições de variáveis, validado antes de importar.
- **E6. Operação:** notificação por e-mail quando um fluxo falha, log de auditoria, `docker compose`, backup/restauração do banco;
  `TRAMA_HOST` fora de `127.0.0.1` só com a autenticação ligada.

*Pronto quando:* duas pessoas editam fluxos diferentes na mesma instalação; um fluxo vai de "dev" para "prod" por um pacote sem edição manual;
uma versão antiga é restaurada pela interface.

### Fase F — Ergonomia e governança
- **F1.** Arrastar para reordenar, recolher contêineres, copiar/colar passos entre fluxos.
- **F2.** Tema escuro (com contraste AA) e saídas reais da última execução no cartão.
- **F3.** Revisão de segurança independente de tudo o que mexe em rede, credenciais, contas e webhooks.
- **F4.** README e capturas de tela.

## Regras que valem para todas as fases

- O isolamento do Python não enfraquece: cada contêiner atende um único trabalho; `--network none`, `--read-only`, usuário sem privilégios,
  limites e vigia continuam; os testes de isolamento de dentro do contêiner continuam passando sem alteração.
- Fluxos, bancos e arquivos exportados existentes continuam abrindo (migração testada quando o formato muda).
- Toda entrada de fora (HTTP, arquivos, respostas de servidores remotos, dados gravados) é não confiável: validada e com teto de tamanho.
