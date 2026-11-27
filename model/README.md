# 최종 차량·리어윙 해석 모델

[TypeB_TEST.rmd](TypeB_TEST.rmd)는 최종 66초 해석에 입력된 RecurDyn 2023 솔버의 텍스트 모델입니다. 텍스트 편집기에서 아래 이름을 찾으면 실제 입력된 요소를 읽을 수 있습니다.

| 이름 | 모델에서의 역할 |
| --- | --- |
| `TRX25_FORCE_REAR_WING_SPEED_SERVO` | 윙 힌지에 제한토크 적용 |
| `TRX25_FORCE_REAR_WING_AERO_ON_CHASSIS` | 차체 공력중심에 등가 항력·다운포스 적용 |
| `TRX25_FORCE_REAR_WING_STATIC_REGULARIZER` | 회전 복원력과 감쇠 |
| `TRX43_REQ_ACTIVE_WING_PROOF` · `TRX43_REQ_ACTIVE_WING_HEALTH` | 차속·윙각·항력·다운포스·토크 출력 |

[타이어 입력](Tire_Files/TRX39_RC48_ASSUMED_V1.tir) · [주행 경로](path.mat) · [구성 코드](../analysis/README.md)

새 솔버 실행에는 호환되는 RecurDyn 라이선스와 원래 차량 예제의 `RD_Driver.dll` 및 `Road_Files/default_flat.rdf`가 필요합니다. 공개본은 입력 구조를 살펴보고 [저장된 Plot](../results/README.md)을 열어 결과를 확인할 수 있도록 구성했습니다. 개인 PC 경로가 들어 있는 Modeler 원본은 로컬에 보관합니다.
