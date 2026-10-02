# Speech To Text

[English](README.md) · **Português (Brasil)**

Segure `Ctrl+Alt+Space`, fale, solte. A transcrição é digitada direto na janela
em que você já estava — Word, Chrome, VS Code, o chat de um jogo. Sem trocar de
janela, sem dançar com a área de transferência.

Segure `Ctrl+Alt+O`, diga "abra o Chrome", "feche o bloco de notas" ou "travar a
tela", solte. A máquina faz, e nada é digitado em lugar nenhum.

Roda no Windows, na bandeja, sem atrapalhar.

Licença MIT. Apenas Windows: a hotkey é um hook de teclado de baixo nível e o
áudio passa pelo `sounddevice`, então hoje não há caminho para macOS ou Linux.

## Instalação

```
pip install -r requirements.txt
```

Pegue uma chave gratuita em <https://console.groq.com/keys> e defina como
variável de ambiente:

```
setx GROQ_API_KEY gsk_...
```

ou clique com o botão direito no ícone da bandeja → `Groq API key` e cole. O
aplicativo guarda em `%APPDATA%\Speech-To-Text\groq_key.txt` (texto puro; se o
computador é compartilhado, esse é o único arquivo que vale proteger).

## Executar

```
python -m stt
```

Verificar a configuração sem subir a interface:

```
python -m stt --check
```

Também mostra a hotkey de comando, se os comandos por voz e a lista de ações de
sistema estão ligados, e quantos aplicativos, pastas e ações foram encontrados.

## Como se comporta

| Você faz | Você recebe |
| --- | --- |
| Segura `Ctrl+Alt+Space` | Ícone da bandeja fica vermelho, overlay mostra medidor de nível ao vivo |
| Fala | Áudio é capturado a 16 kHz mono |
| Solta | Áudio vai pro Whisper da Groq, transcrição é digitada na janela em foco |
| Texto longo | Colado pela área de transferência em vez de digitado caractere a caractere |
| Segura `Ctrl+Alt+O` | Ícone da bandeja fica azul |
| Diz "abra o Chrome" / "feche o bloco de notas" / "travar a tela" | O app abre, fecha, ou a tela trava — e nada é digitado |

Texto curto é digitado com eventos Unicode do `SendInput`, então letras
acentuadas e CJK funcionam e a área de transferência fica intacta. Qualquer coisa
acima de 200 caracteres é colada com Ctrl+V, porque injetar caractere a caractere
é lento e cai em aplicativos mais pesados. Nos dois casos a transcrição também é
copiada para a área de transferência como reserva, e você pode desligar isso.

Se você soltar a tecla sem ter falado nada audível, o app avisa e não insere
texto solto.

## Precisão

`whisper-large-v3` com `temperature=0` é a linha de base de precisão — sem
amostragem, então duas gravações do mesmo áudio dão o mesmo texto. O menu da
bandeja troca para `whisper-large-v3-turbo` se você preferir menos latência em
troca de uma pequena queda de acerto.

O campo **prompt** dá contexto ao modelo. Útil quando você dita nomes próprios,
identificadores de código, ou um idioma que as configurações padrão erram. Vive
em `config.json` como `prompt`:

```json
"prompt": "GitHub, Kubernetes, TypeScript. Dicitação técnica casual."
```

O idioma é fixado por sessão no menu da bandeja; coloque o código certo (`en`,
`pt`, `es`, ...) em vez de `auto` para o modelo parar de detectar errado falas
curtas.

## Command with Voice

Uma segunda hotkey age sobre o que você disse em vez de digitar. Ligue com
**Command with Voice** no menu da bandeja e então:

1. Segure `Ctrl+Alt+O` (o ícone da bandeja fica azul).
2. Diga uma das coisas abaixo.
3. Solte. Acontece, e nada é digitado na janela em foco — a página que você
   estava lendo mantém o conteúdo.

Inglês e português funcionam, misturados à vontade.

| Diga | O que acontece |
| --- | --- |
| `open Chrome`, `abra o Spotify`, `launch Visual Studio Code` | O app abre |
| `open notepad`, `open calc`, `open code` | Qualquer coisa no `PATH` abre |
| `open folder Downloads`, `abrir a pasta Videos` | O Explorer abre a pasta |
| `open Downloads` | O mesmo — pasta é o plano B quando nenhum app bate |
| `open youtube.com`, `open www.example.com` | Abre no navegador padrão |
| `close Chrome`, `feche o bloco de notas`, `quit notepad` | Fecha toda janela cujo título bate |
| `close the folder Downloads` | Fecha a janela do Explorer que a mostra |
| `lock the screen`, `travar a tela` | A estação trava |
| `sleep`, `dormir` | Suspende |
| `empty the recycle bin`, `esvaziar a lixeira` | Esvazia a lixeira |
| `volume up` / `volume down` / `mute` / `unmute` | Teclas de mídia, em broadcast |
| `take a screenshot`, `captura de tela` | PNG em `Pictures\Screenshots` |
| `show desktop`, `mostrar a area de trabalho` | Minimiza tudo |
| `clear the clipboard` | Esvazia a área de transferência |

O overlay diz o que fez, então uma palavra mal ouvida aparece na hora.

### O que ele não faz

Não existe caminho para rodar uma linha de comando qualquer, e a lista não tem
nada de desligar, reiniciar, apagar ou formatar. Toda ação de sistema é
reversível e está escrita em `SYSTEM_ACTIONS`, no `stt/voice_command.py` — adicione
nessa tabela se quiser mais, mas leia o raciocínio antes.

Desligar **Allow system commands** no menu da bandeja deixa aplicativos,
pastas, URLs e fechamento de janela, e recusa o resto com uma mensagem explícita
em vez de cair na busca de apps.

### Como a frase é lida

A transcrição é dividida em verbo e alvo. Verbos de abrir: `open`, `open up`,
`launch`, `start`, `go to`, `run`, `abra`, `abrir`, `abre`, `inicie`,
`iniciar`, `rode`, `executar`, `chame`. Verbos de fechar: `close`, `quit`,
`exit`, `kill`, `feche`, `fechar`, `fecha`, `encerrar`, `sai`, `sair`,
`terminar`. Frases da lista de sistema funcionam sozinhas — `lock the screen` não
tem verbo nenhum.

Enchimento é removido antes da comparação: um `can you` / `por favor` na frente,
um artigo (`the`, `o`, `my`), um `please` / `agora` no fim, e substantivos que não
acrescentam nada. Então `open the chrome browser please` ainda abre o Chrome, e
`open up the notepad, please` ainda abre o Bloco de Notas. Acentos e pontuação são
ignorados na comparação, então `Calculadora` e `calculadora!` são o mesmo pedido
— mas um **caminho falado mantém as maiúsculas**, então
`open folder "C:\Users\Example\Documents"` funciona.

Se a frase não tiver verbo e não for uma de sistema, nada roda — o overlay avisa
em vez de adivinhar.

### Como o nome de um app é resolvido

Em ordem, o primeiro acerto vence:

1. Um apelido de `command_aliases` no `config.json`.
2. Um executável no `PATH` — então `notepad`, `calc` e `code` funcionam mesmo não
   sendo atalhos do Menu Iniciar.
3. Nome exato de um atalho do Menu Iniciar / Store.
4. Um atalho cujo nome começa com o que você disse, ou contém isso como palavra
   inteira — `chrome` acha `Google Chrome`.
5. A grafia mais próxima por distância de edição, acima de um piso de confiança.
   `spotfy` abre o Spotify; `autocad` não abre nada, porque errar é pior do que
   perguntar de novo.

Só se os cinco falharem é que ele tenta o índice de pastas.

O Menu Iniciar é percorrido uma vez e fica em cache por dois minutos.
Desinstaladores, entradas de reparo e shims de console do tipo `pip` são
ignorados, e atalhos do Menu Iniciar têm prioridade sobre `.exe` soltos achados em
`%LOCALAPPDATA%\Programs`.

Quando um nome não resolve, o overlay avisa e oferece as correspondências mais
próximas que achou — `Quer dizer Notepad ou WordPad?`

### Como o nome de uma pasta é resolvido

Um caminho explícito é usado como veio. Caso contrário o nome é comparado com suas
pastas do sistema percorridas **quatro níveis para baixo**, mais uma passada rasa
em `Program Files` — a mesma escada exato → prefixo → palavra → aproximado dos
apps.

A profundidade importa mais do que parece. Pastas de projeto reais se aninham
(`Downloads\organizar\projetos\Speech-To-Text`), então uma varredura de um nível
só acha o contêiner e dá o projeto como ausente. `AppData`, `node_modules`, `venv`
e afins são ignorados, e o match mais raso vence para que uma pasta de primeiro
nível ganhe de uma homônima aninhada.

`%LOCALAPPDATA%` não é buscado de propósito: é quase só cache, e listar fazia
"open Speech-To-Text" cair no diretório de configuração deste próprio app em vez
do projeto. Software instalado já está coberto pelo índice do Menu Iniciar.

A varredura leva cerca de 0,15 s e roda numa thread de fundo na inicialização,
então o primeiro comando falado nunca espera por ela. Os resultados ficam em
cache por dois minutos.

### Como uma janela é casada para fechar

Títulos de janelas visíveis de primeiro nível, normalizados do mesmo jeito:
título exato, depois prefixo, depois uma palavra inteira dentro do título, depois
uma grafia próxima. Todos os achados são fechados, então "close Chrome" fecha
todas as janelas dele. Fechar posta `WM_CLOSE`, que é exatamente o que o X da
barra de título faz, então um app com trabalho não salvo ainda tem chance de
perguntar. Nada nunca é morto à força, e janelas deste próprio app são excluídas
para que um achado aproximado nunca feche o overlay.

### Apelidos

Os nomes do Windows nem sempre batem com como as pessoas falam, e numa instalação
em português "explorador de arquivos" é `File Explorer` no Menu Iniciar. Dê um
destino para a palavra que você realmente fala:

```json
"command_aliases": {
  "browser": "chrome",
  "planilha": "excel",
  "terminal": "Windows Terminal"
}
```

O valor de um apelido é um nome de app, resolvido do mesmo jeito — não um caminho
de arquivo.

## Hotkey

O padrão é `Ctrl+Alt+Space`. Troque pelo menu da bandeja —
**Set dictation hotkey...** abre uma janela que espera você apertar o combo; a
tecla é salva em `config.json` e vale na hora, sem reiniciar.

Ou na inicialização:

```
python -m stt --hotkey "ctrl+shift+m"
```

Só uma tecla não-modificadora, então nada de acordes como
`ctrl+alt+shift+space`. O combo é salvo em `config.json` e reaproveitado na
próxima vez que abrir.

**Set command hotkey...** faz o mesmo pelo combo do `Ctrl+Alt+O`, que também tem a
flag `--command-hotkey`:

```
python -m stt --command-hotkey "ctrl+win+j"
```

As duas não podem ser o mesmo combo, senão os dois listeners disparariam num
único aperto físico. Só uma tecla não-modificadora por combo.

Gatilhos com letra funcionam com modificador segurado: o Windows reporta
`Ctrl+Alt+O` com um caractere embaralhado e sem nome de tecla, então o matcher
cai para o virtual key code. Isso também cobre layouts em que o AltGr reescreve
a letra.

## Iniciar com o Windows

Bandeja → `Start with Windows` escreve em `HKCU\...\Run`. Ele lança o
`pythonw.exe` quando existe, para não piscar um console no login.

## Configuração

`%APPDATA%\Speech-To-Text\config.json`, criado na primeira execução:

| Chave | Padrão | O que faz |
| --- | --- | --- |
| `hotkey` | `["ctrl","alt","space"]` | Combo de falar-segurando |
| `command_hotkey` | `["ctrl","alt","o"]` | Combo do modo de comando por voz |
| `command_with_voice` | `true` | Liga/desliga os comandos por voz |
| `command_system_enabled` | `true` | Permite as ações de sistema da lista |
| `command_aliases` | `{}` | Nome falado → nome de app, ex. `{"browser":"chrome"}` |
| `model` | `whisper-large-v3` | Id do modelo na Groq |
| `language` | `en` | Código de idioma, ou `auto` |
| `prompt` | `""` | Contexto inicial para o modelo |
| `max_seconds` | `120` | Teto por gravação |
| `min_rms` | `0.0035` | Abaixo disso, tratado como silêncio |
| `auto_type` | `true` | Liga/desliga geral |
| `clipboard_backup` | `true` | Copia cada transcrição também |
| `trailing_space` | `true` | Acrescenta espaço depois de cada inserção |
| `type_delay_ms` | `6` | Atraso entre caracteres injetados |
| `sample_rate` | `16000` | Taxa de captura (a nativa do Whisper) |

Valores desconhecidos ou inválidos são reparados na leitura, então um erro de
digitação no JSON não impede o app de iniciar. Um arquivo salvo com byte-order
mark (o que o Bloco de Notas escreve) é lido sem problema.

Configs antigos se migram sozinhos: `open_hotkey`, `open_with_voice` e
`open_aliases` são renomeados para as chaves `command_*` na primeira leitura,
mantendo os valores, e o arquivo é reescrito para os nomes antigos não voltarem.
Uma chave que você já configurou à mão sempre ganha da antiga.

## Testes

```
python -m pytest tests -q
```

256 testes: máquina de estados da hotkey (repetição de tecla, modificadores
soltos, key-up perdido, teclas de letra com Alt, dois combos convivendo),
reparo de config, migração de chaves antigas e tolerância a BOM, codificação
WAV, o formato da requisição à Groq e o mapeamento de erros, roteamento de
entrega, o guarda de silêncio, parsing de comando (abrir, fechar, pasta, URL e
frases de sistema, em dois idiomas), resolução de app / pasta / janela, depth e
orçamento do índice de pastas, roteamento de abertura e fechamento, o guarda da
lista, os utilitários Win32 de janela, o codificador PNG escrito à mão, captura
de hotkey pela bandeja, e o pipeline completo aperta → transcreve → entrega contra
um event loop Qt de verdade.

Os testes de pipeline precisam de um plugin de plataforma Qt. Numa máquina sem
tela:

```
set QT_QPA_PLATFORM=offscreen
```

`tools/verify_typing.py` é uma verificação manual de ponta a ponta de que o texto
injetado chega mesmo numa janela real em foco. Ele cria uma janela Win32 crua com
um controle EDIT, injeta através de `stt.typing` e compara o que chegou. Rode
solto ou um console rouba o foco e corrompe o resultado:

```
Start-Process -WindowStyle Hidden python -ArgumentList tools\verify_typing.py
```

A captura de tela é a outra coisa que vale checar à mão, já que não dá para
afirmar contra um device context falso:

```
python -c "from stt import screen; print(screen.save_screenshot())"
```

## Limites conhecidos

- A detecção é um hook de teclado de baixo nível, então o app precisa estar
  rodando; não há serviço do Windows nem prompt de UAC elevado.
- A gravação para no primeiro key-up do gatilho *ou* de qualquer modificador,
  então soltar o Ctrl antes encerra o hold em vez de gravar silêncio.
- A gravação é interrompida à força em `max_seconds`, caso um key-up se perca por
  mudança de foco ou bloqueio de sessão. Um overlay travado é pior do que uma
  ditação longa cortada.
- Injetar em janelas elevadas (rodando como administrador) falha em silêncio a
  partir de um processo não elevado. Suba o app como administrador se você
  ditar em terminais ou instaladores admin.
- Escala de DPI muito alta pode borrar o overlay; ele renderiza na resolução
  lógica que o Qt informa.
- O Whisper transcreve fala, não intenção de formatação. Ditar "nova linha" dá
  as palavras, não uma quebra de linha. Pausas viram pontuação pelo modelo.
- Comandos por voz só enxergam o que `PATH`, o Menu Iniciar e suas próprias
  pastas anunciam. Apps da Store nunca abertos não expõem atalho nenhum, então o
  apelido é o caminho de entrada. Ele casa nomes, não a busca do Windows, então
  uma instalação em português ainda quer nomes de app em inglês a não ser que
  você crie apelidos.
- Pastas são achadas até quatro níveis abaixo das suas pastas do sistema. Um
  projeto mais fundo que isso precisa de caminho explícito:
  `open folder C:\...\...\...`.
- Duas pastas com o mesmo nome resolvem para a mais rasa. O overlay mostra qual
  pasta escolheu, então diga algo mais específico se não foi a certa.
- Fechar uma janela posta `WM_CLOSE`, então um app que pede para salvar vai
  perguntar. Não há como matar um processo à força, por design.
- `sleep` pede ao Windows para suspender. Uma máquina sem hibernação em vez disso
  desliga, e essa é a decisão do SO, não deste app.
- Uma captura de tela é o monitor principal na resolução nativa, salva em PNG
  sem compressão. Não há biblioteca de imagem envolvida, então leva cerca de um
  segundo.
- O áudio vai para os servidores da Groq. Se isso importa para seus dados, diga
  que a etapa de transcrição pode ser trocada por um `faster-whisper` local sem
  tocar no código de captura ou entrega.