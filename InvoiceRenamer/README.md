# InvoiceRenamer 개발자용 통합본

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
| app/monthly_close.py | 명세서 가져오기와 기간 검산, 거래번호, 증빙 대조, 메모, 마감 이력, 독일어 제출 ZIP |
| app/tax_documents.py | XRechnung과 ZUGFeRD의 구조화된 필드 및 PDF 세무 정보 |
| app/advisor_workbook.py | 독일식 CSV 금액과 Excel 숫자 셀, 증빙 상대 링크 |
| app/workspace_backup.py | 보조 자료 저장 및 전체 작업 백업·복원 |
| app/setup_ocr.py | 독일어·영어 OCR 언어 파일 준비 |
| app/Beispiele | 독일식 금액이 들어 있는 공개용 합성 PDF·CSV 예시 |
| tools/windows-installer.cmd.in | 설치용 CMD 원본. 배포 스크립트가 ZIP과 해시를 삽입 |
| tools/build_release.py | 현재 소스로 설치용 CMD와 Windows ZIP 생성 |
| tools/smoke_test.py | 화면 없이 실행하는 기능 검증 |

## 저장 기록과 업데이트

Windows의 사용자 데이터는 LOCALAPPDATA/InvoiceRenamer 아래에 있습니다. monthly-close.json에는 거래번호, 명세서 설정, 연결, 마감본 이력, 내부·독일어 메모가 저장됩니다. invoice-edits.json은 증빙 내용의 SHA-256을 키로 수정값을 보관합니다. 첨부는 같은 사용자 데이터 폴더의 attachments에 저장합니다. 전체 복원본은 상태 파일의 attachment_root를 이용할 수 있습니다.

이전 기록에 없는 새 필드는 기본값으로 처리합니다. 거래 식별 규칙이나 파일 내용 해시를 바꾸면 기존 거래번호와 연결을 잃을 수 있으므로 기존 기록을 연 상태에서 검증합니다. 제출용 ZIP과 전체 작업 백업은 각각 다른 용도로 사용합니다. 제출용에는 내부 메모를 넣지 않습니다.

## 빌드와 검증

    python tools/smoke_test.py
    python tools/build_release.py

dist 폴더에 invoice-renamer-monthly-close-windows.zip과 InvoiceRenamer-Setup.cmd가 만들어집니다. 새 앱 파일을 배포할 때는 tools/build_release.py의 APP_FILES 목록을 갱신합니다. 의존성을 바꾸면 requirements.txt와 app/Start.bat의 버전도 함께 바꿉니다. 설치 템플릿이 다운로드하는 Python의 버전을 바꿀 때는 공식 설치 파일의 경로와 서명 확인도 검증합니다.

스모크 테스트는 합성 자료로 PDF·CSV 읽기, 독일식 금액, 증빙 대조, 원본 위치, 마크다운, 제출 ZIP·Excel, 보조 자료, 전체 복원을 확인합니다. Windows 설치 실행과 Excel의 실제 링크 열기는 별도 확인 항목입니다. 테스트할 때는 예시 명세서인 Beispiele/Karte.csv를 열고 증빙 폴더에는 Beispiele/Belege를 선택하세요.

## 버전 및 업데이트 복원

새 버전에서는 app/VERSION을 갱신합니다. release_info.py는 버전과 개인정보를 포함하지 않는 세션 오류 진단을 관리합니다. 설치용 CMD는 기존 프로그램과 사용자 작업 기록 JSON을 교체 전에 update-backups에 복사합니다. Restore-PreviousVersion.cmd로 프로그램만 또는 작업 기록까지 복원할 수 있습니다. 첨부와 원본 자료 백업은 앱의 전체 작업 백업을 사용합니다. Windows 실제 설치·복원 검증은 배포 전에 별도로 수행합니다.
