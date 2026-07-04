# yscha-M8 아이들 프리즈 트러블슈팅 기록

## 개요

| 항목 | 내용 |
|------|------|
| 발생일 | 2026-07-04 (토) |
| 증상 | 아침 기상 시 미니PC 팬 폭주 + 발열, SSH·모니터 모두 무응답 |
| 정지 시각 | 2026-07-04 07:53:32 KST (로그 마지막 기록) |
| 하드웨어 | GMKtec NucBox M8 / AMD Ryzen 5 PRO 6650H |
| 커널 | 6.17.0-35-generic (x86_64) |
| 결론 | AMD Ryzen C-state(C6) 아이들 프리즈 |
| 조치 | 커널 부팅 파라미터로 깊은 C-state 진입 차단 |

---

## 1. 증상

아침 8시 30분경 미니PC(`yscha-M8`)의 팬이 최대로 돌며 뜨거운 열기를 뿜고 있었다.
SSH 접속과 연결된 모니터 화면 모두 응답하지 않아 상태 확인이 불가능했고,
전원 강제 재부팅으로 복구했다.

## 2. 진단 과정

### 2-1. 정지 시점 특정

저널 로그가 영구 저장(`/var/log/journal/` 존재)되어 있어 이전 세션 로그를 확보할 수 있었다.

```bash
journalctl --list-boots
```

문제 세션(`-1`)의 마지막 로그가 `2026-07-04 07:53:32`에서 정상 종료(shutdown) 메시지 없이
뚝 끊긴 것을 확인. 다음 부팅은 08:26:21(수동 재부팅)로, 약 30분간 정지 상태였다.

로그 끝부분에서 기록 간격이 점점 벌어지다가(07:52:11 → 07:52:20 → 07:52:37 → 07:53:32)
완전히 끊기는 패턴을 보였다. 이는 시스템이 서서히 반응을 잃다가 완전히 얼어붙는(hard hang)
전형적 양상이다.

### 2-2. 원인 후보 소거

**메모리 부족(OOM) — 배제**

```bash
journalctl -b -1 | grep -iE "out of memory|oom-kill|oom_reaper|killed process"
# → 결과 없음
```

OOM 킬러가 발동했다면 반드시 로그를 남기므로, 메모리 부족은 아니다.

**커널 soft lockup / RCU stall / thermal / MCE — 배제**

```bash
journalctl -b -1 -k | grep -iE "soft lockup|hung task|rcu.*stall|thermal|throttl|mce"
# → 부팅 시점 초기화 메시지만 존재, 정지 시점 이벤트 없음
```

커널이 스스로 문제를 감지해 로그를 남길 틈도 없이 통째로 멈췄다는 의미.

**소프트웨어 폭주 — 배제 (결정적 근거)**

정지 3분 전(07:50:05) sysstat 수집 데이터:

```bash
sar -u -f /var/log/sysstat/sa04 -s 07:40:00 -e 07:54:00   # CPU
sar -q -f /var/log/sysstat/sa04 -s 07:40:00 -e 07:54:00   # 부하평균
```

| 지표 | 값 | 해석 |
|------|-----|------|
| CPU idle | 99.54% | 거의 아무 작업 없음 |
| load avg (1/5/15분) | 0.02 / 0.05 / 0.01 | 완전히 한가함 |
| runq-sz | 0 | 대기 프로세스 없음 |

정지 직전 시스템이 완전히 놀고 있었으므로, 자동매매 스크립트를 포함한
무한루프·메모리 누수 등 소프트웨어 폭주 가설은 완전히 배제된다.

### 2-3. 발열 확인 — 원인 아님

재부팅 후 센서 확인(`lm-sensors` 설치 후 `sensors`):

| 센서 | 온도 |
|------|------|
| CPU (k10temp Tctl) | 46.1°C |
| NVMe SSD (Composite) | 47.9°C (crit 109.8°C) |
| GPU edge | 40.0°C |

온도는 모두 정상 범위. 무엇보다 07:50 시점에 CPU가 idle 상태였으므로
부하 발열로 죽은 것이 아니다. **팬 폭주는 원인이 아니라 시스템 정지 후
펌웨어 fail-safe로 팬이 최대 회전한 결과**로 판단된다.

## 3. 원인 확정: AMD Ryzen C-state 아이들 프리즈

수집된 근거가 리눅스에서 오래 알려진 **AMD Ryzen 아이들 프리즈** 문제와 정확히 일치했다.

- CPU가 idle 상태에서 깊은 절전 단계(C6)로 진입
- 리눅스 커널이 해당 코어를 정상적으로 깨우지 못함(wake 실패)
- 응답 불능 코어가 연쇄적으로 시스템 전체를 정지시킴

**본 케이스가 일치하는 3중 근거**

1. 정지 직전 CPU idle 99.5%, load 0.02 → 깊은 절전 진입 조건 충족
2. 커널까지 통째로 hang, 로그 무흔적 → C-state 탈출 실패의 전형
3. 팬 폭주 → 원인이 아니라 정지 후 fail-safe 결과

## 4. 조치: 커널 부팅 파라미터 추가

`/etc/default/grub` 수정:

```
# 변경 전
GRUB_CMDLINE_LINUX_DEFAULT="quiet splash"

# 변경 후
GRUB_CMDLINE_LINUX_DEFAULT="quiet splash processor.max_cstate=1 idle=nomwait"
```

적용:

```bash
sudo update-grub
sudo reboot
```

적용 확인:

```bash
cat /proc/cmdline
# BOOT_IMAGE=... quiet splash processor.max_cstate=1 idle=nomwait vt.handoff=7
```

### 파라미터 설명

**`processor.max_cstate=1`**

CPU가 진입할 수 있는 가장 깊은 C-state를 C1(얕은 절전)까지로 제한한다.
문제의 원인인 C6 등 깊은 절전으로는 아예 진입하지 못하게 막아,
"못 깨어나는" 상황 자체를 차단한다. 프리즈 근본 원인을 막는 핵심 옵션.

**`idle=nomwait`**

CPU를 재우는 방식을 바꾼다. 리눅스 기본값인 `MWAIT` 명령어 기반 절전 진입이
일부 AMD 하드웨어에서 프리즈를 유발하므로, MWAIT를 쓰지 않고
더 단순·안전한 방식(HALT 등)으로 코어를 재우게 한다.

**두 옵션의 관계**: 중복이 아닌 상호 보완.
`max_cstate=1`이 위험한 절전 단계 진입을 막고,
`idle=nomwait`이 얕은 절전에서도 안전한 진입 방식을 쓰게 하여 이중 방어한다.

### 트레이드오프

깊은 절전을 포기하므로 idle 시 소비전력과 발열이 다소 상승한다.
24시간 매매 서버는 절전보다 안정성이 우선이므로 감수 가능한 수준.
추후 idle 온도가 60°C대 초중반을 지속적으로 넘으면 재조율 검토.

## 5. 검증 및 관찰

재부팅 후 파라미터가 정상 적용됨을 `cat /proc/cmdline`으로 확인.

향후 며칠간 아침마다 아래로 세션 연속성을 확인한다:

```bash
journalctl --list-boots | tail -5
```

하나의 부팅 세션이 끊기지 않고 계속 이어지면 정상.
중간에 로그가 뚝 끊긴 새 부팅이 생기면 재발이므로 추가 조치 필요.

## 6. 백업 플랜 (재발 시)

현재 설정으로도 프리즈가 재발하면, 원인이 C-state가 아니라
amdgpu(GPU) 절전 쪽일 가능성이 높다. 이 경우 GRUB에 추가 시험:

```
amdgpu.dpm=0
```

또한 GMKtec M8 BIOS에서 **Global C-state Control / C-States** 항목을
Disabled로 설정하는 것도 커널 파라미터와 병행 가능한 대책이다.

---

*작성일: 2026-07-04*
