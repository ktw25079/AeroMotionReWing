# 모델 구성과 검증 코드

실제 개발에 사용한 Stage 43 스크립트입니다. 모델 생성·보정에는 RecurDyn 2023의 Python 연동과 각 단계의 입력 Modeler 파일이 필요합니다.

| 코드 | 확인할 구현 |
| --- | --- |
| [윙 구동 구성](stage43_repair_active_wing.py) | 기존 강제 회전·내부력 해제, 제한토크 서보와 차체 등가 공력 적용 |
| [제동 연동](stage43_calibrate_closed_loop_brake.py) | 폐루프 브레이크 신호를 윙 상태 판정에 연결 |
| [출력 채널 탐색](stage43_list_channels.py) · [결과 검사](stage43_validate_active_result.py) | Plot에서 채널을 읽고 윙각·토크·해석 종료 조건 검사 |
| [공력 대조 판정](stage43_accept_solver_aero.py) | 계산한 힘과 솔버 출력 간 차이를 허용치와 비교 |
| [모델 파일 목록 생성](stage43_make_manifest.py) | 결과 패키지의 경로·크기·해시 기록 |

공력 대조 판정은 Python 3만으로 재현할 수 있습니다. 저장소 루트에서 다음을 실행합니다.

```bash
python analysis/stage43_accept_solver_aero.py results/stage43_r2_solver_applied_aero_validation.json acceptance.local.json
```

생성된 JSON의 `passed`와 항목별 판정을 [보관 결과](../results/stage43_r2_solver_aero_acceptance.json)와 비교합니다. 출력 파일의 입력 경로는 실행한 PC에 따라 달라집니다.
