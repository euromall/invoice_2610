# InvoiceAuto (새 송장 프로그램 v1)

기존 프로그램과 같은 폴더 구조를 그대로 씁니다.

```
프로그램 폴더/
  InvoiceAuto.exe
  InvoiceAuto.ini        (설정저장 시 생성)
  Base/                  Base.xlsx + 결과 양식 12개 + AccountBook.xlsx
  db/euromall.db         회계·재고 DB (작업 전 db/backup/ 에 자동 백업)
  ShippingInvoice/       입력 엑셀 (작업 후 backup/ 에 복사)
  Result/Profoma_YYMMDD  견적서용 결과
  Result/Bill_YYMMDD     청구서용 결과
```

## exe 만들기 (Windows)
1. python.org 에서 Python 3.12 설치 (설치 화면에서 "Add python.exe to PATH" 체크)
2. 이 폴더에서 `build_exe.bat` 실행
3. `dist\InvoiceAuto.exe` 를 프로그램 폴더에 복사

exe 없이 바로 실행하려면 `run.bat` (소스 폴더를 프로그램 폴더 안에 두거나, `py main.py <프로그램 폴더>`).

## 영문주소 (개인 인보이스)
- 프로그램 내 변환: 인터넷 없이 변환
- 행정안전부 API: business.juso.go.kr 영문주소 승인키 입력. 실패하면 자동으로 프로그램 내 변환 사용

## PDF
Microsoft Excel 이 있으면 Excel 로, 없으면 LibreOffice 로 저장합니다.

## 기존 프로그램과 병행 테스트
1. 메인 화면의 **테스트 모드**를 켭니다. 결과는 `Result_test/`, 회계·재고는 `db/euromall_test.db`(처음 한 번 euromall.db 를 복사)에 기록되고, 실제 `euromall.db` 와 `Result/` 는 건드리지 않습니다. 입력 파일 백업도 하지 않습니다.
2. 같은 입력 엑셀로 기존 프로그램과 새 프로그램을 각각 실행합니다.
3. **결과 비교**를 누르고 기존 결과 폴더(`Result/Profoma_YYMMDD`)와 새 결과 폴더(`Result_test/Profoma_YYMMDD`)를 고릅니다.
   새 결과 폴더에 `비교결과_….xlsx` 가 저장됩니다. 파일별 다른 칸과, 작업일자의 회계 금액 비교가 들어 있습니다.
   기존 프로그램의 오류를 고쳐서 생기는 차이는 "알려진 차이" 시트에 정리돼 있습니다.
4. 테스트 DB를 처음 상태로 돌리려면 **테스트 DB 새로 복사**를 누릅니다.
