# InvoiceRenamer 개발자용 통합본

독일 세무 월마감용 프로그램입니다. 은행·카드 명세서 거래에 인보이스를 연결하고, 세무사에게 전달할 독일어 ZIP과 Excel 대조표를 만듭니다.

## v1.2.2 변경 사항 (2026-11-26)

| 구분 | 내용 |
|---|---|
| PDF 명세서 번호 표시 | PDF로 불러온 은행·카드 명세서는 세무사 ZIP의 `Kontoauszuege/`에 원본과 함께 `…_nummeriert.pdf` 사본을 넣습니다. 사본에는 각 거래 줄을 옅은 노란색으로 표시하고 왼쪽에 `[611-001]` 같은 거래번호를 찍습니다. 줄은 거래일(일.월)로 시작하고 금액이 같은 줄을 찾아 표시하며, 찾지 못한 거래 수는 사본 첫 줄과 `05_Kontoauszuege.csv`에 적습니다. 원본 파일은 바꾸지 않습니다. 2단계 '번호 표시 PDF 명세서 보기'로 미리 볼 수 있습니다. |

## v1.2.1 변경 사항 (2026-11-26)

| 구분 | 내용 |
|---|---|
| 상세 창 | 메모·기록, 인보이스 정보 편집, 점검표, 미리보기 등 모든 상세 창이 별도 창 대신 프로그램 화면 오른쪽 패널로 열립니다(`ui_theme.InlineDialog`). 위쪽에 제목과 '닫기', Esc로 닫기, 저장 버튼은 파란색으로 통일했습니다. |
| 메모·기록 | 거래 요약, 내 작업 메모, 작업 기록(목록+새 기록 한 줄), 후속 처리(상태·예정일), 세무사 전달 메모(독일어 문구 버튼·초안 만들기)를 카드 4개로 나눴습니다. Ctrl+S로 저장합니다. |
| 안내 메시지 | "거래를 선택하세요" 같은 안내가 아래 상태줄에만 나오던 것을 화면 가운데 팝업으로 보여 줍니다. Enter·Esc·확인으로 닫습니다. |
| 오류 수정 | v1.1.0부터 있던 문제: 메모·기록 저장 시 오류가 나며 저장되지 않던 문제(정의되지 않은 변수)를 고쳤습니다. 독일어 초안을 메모에 넣을 때 줄바꿈 대신 `\n` 글자가 들어가던 문제와 '다시 열지 못한 명세서' 안내의 줄바꿈도 고쳤습니다. |

## v1.2.0 변경 사항 (2026-11-26)

| 구분 | 내용 |
|---|---|
| 화면 | 한 창에서 왼쪽 5단계(증빙 모으기·정리 → 명세서 불러오기 → 증빙 매칭 → 누락 점검 → 세무사 제출)로 이동합니다. 상단에 월 선택, 진행 막대와 거래 상태 수(대조 완료·이체·환불·확인 필요·증빙 없음)를 표시합니다. 행은 상태별 색(초록·보라·주황·빨강·회색)으로 구분하고, 단계마다 주 버튼 1개만 파란색으로 강조하며 나머지는 '더보기'로 옮겼습니다. 기능과 저장 기록 형식은 v1.1.1과 같습니다. |
| 시작 | EXE는 실행 즉시 시작 화면(스플래시)을 보여 주고, 준비가 끝나면 창을 다른 창 앞으로 띄웁니다. |
| 중복 실행 | Windows에서 이미 실행 중이면 새로 열지 않고 열려 있는 창을 앞으로 가져옵니다. 연습 모드는 예외입니다. |
| 필수 항목 | 1단계에서 발행일·업체명·총액·인보이스 번호를 못 읽은 파일은 빨간색 '필수 누락: …'으로 표시합니다. 번호가 없어도 250 EUR 이하면 소액영수증(§33 UStDV) 확인 대상으로 주황색 표시합니다. |
| 인보이스 아님 의심 | 글자는 있는데 Rechnung·MwSt·Netto·Total 같은 인보이스 단어가 거의 없는 파일은 회색 '인보이스 아님 의심'으로 표시합니다. '더보기 → 인보이스 아닌 파일 → 제외 폴더'으로 `제외` 하위 폴더에 옮길 수 있고(삭제하지 않음), '정리 되돌리기'로 되돌릴 수 있습니다. `제외` 폴더는 목록에서 빠집니다. |

## v1.1.1 변경 사항 (2026-11-26)

| 구분 | 내용 |
|---|---|
| 파일명 규칙 | 세무사용 ZIP과 같은 형식으로 바꿨습니다. 정리: `58.39_26-11-25_greenist GmbH_Nr_R1044176_invoice.pdf`, 매칭완료 폴더로 이동 후: `[611-001]_58.39_26-11-25_greenist GmbH_Nr_R1044176_invoice.pdf`. 원래 파일 이름은 `original-names.json`에 내용 해시 기준으로 기억하고, 이전 형식(`__원본__`) 파일도 원래 이름을 찾아 새 형식으로 바꿉니다. 같은 이름은 ` (2)`를 붙입니다. |
| 총액 인식 | 기존 라벨로 총액을 찾지 못할 때만 Gesamtbetrag, Gesamtsumme, Rechnungssumme, Zahlbetrag, zu zahlen, Gesamt, Summe, Total, Amount due 등을 추가로 확인합니다. 기존 라벨로 읽히던 인보이스의 결과는 바뀌지 않습니다. 추가 라벨에서 금액이 여러 개면 기존처럼 '확인 필요'로 둡니다. |
| 전자 인보이스 | 파일명 변경 화면도 XRechnung `.xml`을 목록에 표시하고, ZUGFeRD/Factur-X PDF는 내장 XML의 발행일·업체명·총액·번호를 먼저 사용합니다. XML 정보가 부족한 PDF는 기존 텍스트·OCR 방식으로 읽습니다. |
| 가져오기 오류 | 세무사용 `04_Importfehler.csv`의 검산 오류 행에 금액이 빠지던 문제를 고쳤습니다. |
| CSV 인코딩 | 새로 불러오는 CSV는 UTF-8 다음에 독일식 cp1252를 먼저 판단하고, 읽은 문자가 독일·서유럽 문자가 아니면 cp949로 읽습니다. 이미 불러온 적 있는 명세서 파일은 거래번호와 연결이 바뀌지 않도록 이전 방식으로 읽습니다. |
| 업체 인식 | 문서 머리글로 업체를 알아보는 규칙을 `%LOCALAPPDATA%\InvoiceRenamer\seller-patterns.json`에 추가할 수 있습니다. 예: `{"^\\s*Muster GmbH\\b": "Muster GmbH"}`. 기존 내장 규칙은 그대로 동작합니다. |
| 수정값 파일 | `invoice-edits.json`이 손상되면 모든 파일을 'PDF 읽기 실패'로 표시하던 대신, 손상된 파일 위치를 알려 줍니다. |
| 이전 버전 복원 | 복원할 때 새 버전에만 있던 파일을 지우고, 실행 중인 복원 스크립트 자신은 덮어쓰지 않습니다. |
| EXE | Windows용 단일 실행 파일(`InvoiceRenamer.exe`)을 만들 수 있습니다. 독일어·영어 OCR 파일과 예시 자료가 포함되며, 작업 기록은 설치판과 같은 `%LOCALAPPDATA%\InvoiceRenamer`를 사용합니다. |

알려진 제한: 거래번호 형식(`611-001`, 연도 끝자리+월)은 기존 기록과의 호환을 위해 유지했습니다. 정리 되돌리기는 창을 연 동안만 가능합니다.

## EXE 만들기

GitHub Actions의 `invoice-renamer-exe` 워크플로가 Windows에서 테스트 후 `InvoiceRenamer.exe`, 설치용 CMD, Windows ZIP을 만들어 Artifact로 올립니다. 직접 만들려면 Windows에서 `tools\build_exe.bat`을 실행합니다. 서명되지 않은 EXE라 처음 실행할 때 Windows SmartScreen 경고가 나올 수 있습니다.

현재 배포판의 전체 앱 소스, 예시 자료, 배포 도구를 묶었습니다. 앱은 Python과 Tkinter를 사용합니다.

## 개발 환경

Python 3.10 이상과 Tcl/Tk가 필요합니다. 가상 환경을 활성화한 뒤 다음을 실행합니다.

    python -m venv .venv
    python -m pip install -r requirements.txt
    python app/setup_ocr.py
    python app/invoice_renamer.py

OCR 언어 파일은 첫 다운로드 이후 app/tessdata에서 읽습니다. 텍스트 PDF와 CSV 기능은 OCR 없이 시험할 수 있습니다. Windows에서는 app/Start.bat도 사용할 수 있습니다.

테스트에서 실제 작업 기록과 분리하려면 앱 실행 전에 LOCALAPPDATA를 테스트용 디렉터리로 지정합니다. 예를 들어 PowerShell에서 다음 명령을 사용합니다.

    $env:LOCALAPPDATA = Join-Path $PWD '.test-user'

## 주요 코드

| 파일 | 역할 |
|---|---|
| app/invoice_renamer.py | PDF·이미지 OCR, 금액 정규화, 파일명과 발행월 정리, 마크다운, 원본 위치 |
| app/ui_shell.py | 5단계 메인 창, 상단 진행 표시, 시작 화면·중복 실행 방지 |
| app/ui_theme.py | 색상, 글꼴, 버튼·표 스타일, 상태 색 |
| app/monthly_close.py | 명세서 가져오기와 기간 검산, 거래번호, 증빙 대조, 메모, 마감 이력, 독일어 제출 ZIP |
| app/tax_documents.py | XRechnung과 ZUGFeRD의 구조화된 필드 및 PDF 세무 정보 |
| app/advisor_workbook.py | 독일식 CSV 금액과 Excel 숫자 셀, 증빙 상대 링크 |
| app/workspace_backup.py | 보조 자료 저장 및 전체 작업 백업·복원 |
| app/setup_ocr.py | 독일어·영어 OCR 언어 파일 준비 |
| app/Beispiele | 독일식 금액이 들어 있는 공개용 합성 PDF·CSV 예시 |
| tools/windows-installer.cmd.in | 설치용 CMD 원본. 배포 스크립트가 ZIP과 해시를 삽입 |
| tools/build_release.py | 현재 소스로 설치용 CMD와 Windows ZIP 생성 |
| tools/smoke_test.py | 화면 없이 실행하는 기능 검증 |
| tools/regression_test.py | v1.1.1·v1.2 수정 사항 검증 |
| tools/splash.png | EXE 시작 화면 이미지 |

## 저장 기록과 업데이트

Windows의 사용자 데이터는 LOCALAPPDATA/InvoiceRenamer 아래에 있습니다. monthly-close.json에는 거래번호, 명세서 설정, 연결, 마감본 이력, 내부·독일어 메모가 저장됩니다. invoice-edits.json은 증빙 내용의 SHA-256을 키로 수정값을 보관합니다. 첨부는 같은 사용자 데이터 폴더의 attachments에 저장합니다. 전체 복원본은 상태 파일의 attachment_root를 이용할 수 있습니다.

이전 기록에 없는 새 필드는 기본값으로 처리합니다. 거래 식별 규칙이나 파일 내용 해시를 바꾸면 기존 거래번호와 연결을 잃을 수 있으므로 기존 기록을 연 상태에서 검증합니다. 제출용 ZIP과 전체 작업 백업은 각각 다른 용도로 사용합니다. 제출용에는 내부 메모를 넣지 않습니다.

## 빌드와 검증

    python tools/smoke_test.py
    python tools/regression_test.py
    python tools/build_release.py

dist 폴더에 invoice-renamer-monthly-close-windows.zip과 InvoiceRenamer-Setup.cmd가 만들어집니다. 새 앱 파일을 배포할 때는 tools/build_release.py의 APP_FILES 목록을 갱신합니다. 의존성을 바꾸면 requirements.txt와 app/Start.bat의 버전도 함께 바꿉니다. 설치 템플릿이 다운로드하는 Python의 버전을 바꿀 때는 공식 설치 파일의 경로와 서명 확인도 검증합니다.

스모크 테스트는 합성 자료로 PDF·CSV 읽기, 독일식 금액, 증빙 대조, 원본 위치, 마크다운, 제출 ZIP·Excel, 보조 자료, 전체 복원을 확인합니다. Windows 설치 실행과 Excel의 실제 링크 열기는 별도 확인 항목입니다. 테스트할 때는 예시 명세서인 Beispiele/Karte.csv를 열고 증빙 폴더에는 Beispiele/Belege를 선택하세요.

## 버전 및 업데이트 복원

새 버전에서는 app/VERSION을 갱신합니다. release_info.py는 버전과 개인정보를 포함하지 않는 세션 오류 진단을 관리합니다. 설치용 CMD는 기존 프로그램과 사용자 작업 기록 JSON을 교체 전에 update-backups에 복사합니다. Restore-PreviousVersion.cmd로 프로그램만 또는 작업 기록까지 복원할 수 있습니다. 첨부와 원본 자료 백업은 앱의 전체 작업 백업을 사용합니다. Windows 실제 설치·복원 검증은 배포 전에 별도로 수행합니다.
