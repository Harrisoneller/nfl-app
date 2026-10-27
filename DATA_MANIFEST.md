# nflverse data manifest

Public files from [nflverse-data](https://github.com/nflverse/nflverse-data) releases. Tags move. Each file is pinned by the release `updated_at` below and by its SHA-256. Market columns on `games.csv` (`spread_line`, `total_line`, moneylines, odds) are grading columns only. They are not model inputs. `games.csv` has no ESPN BET open; that column is blank in the per-game file.

2014 play-by-play, rosters, snaps, depth charts, and injuries are included because the locked preregistration allows 2014 to inform the 2015 prior, and the frozen v6 starter and continuity path needs those auxiliary files. 2014 is not graded. No 2026 file was downloaded.

## Release tags

| Tag | Release id | Published | Updated (pin) |
| --- | ---: | --- | --- |
| `pbp` | 58152862 | 2022-01-28T02:12:09Z | 2026-10-26T16:29:22Z |
| `schedules` | 251386473 | 2025-10-01T11:19:34Z | 2026-10-27T00:16:57Z |
| `players` | 69785162 | 2022-06-19T05:36:22Z | 2026-10-26T16:04:27Z |
| `rosters` | 58152863 | 2022-01-28T02:12:10Z | 2026-10-26T14:13:10Z |
| `snap_counts` | 58153010 | 2022-01-28T02:15:02Z | 2026-10-26T11:11:01Z |
| `injuries` | 58152949 | 2022-01-28T02:13:49Z | 2026-10-26T14:07:23Z |
| `depth_charts` | 58152948 | 2022-01-28T02:13:47Z | 2026-10-26T14:07:57Z |

## Files

| Release | Asset | Local path | Bytes | SHA-256 |
| --- | --- | --- | ---: | --- |
| `depth_charts` | `depth_charts_2014.parquet` | `aux/depth_2014.parquet` | 435044 | `f4ed3b95c6a5c2e82243bcaffdb2fdb1c64313989aa55044bd4f33490a6abe96` |
| `depth_charts` | `depth_charts_2015.parquet` | `aux/depth_2015.parquet` | 484443 | `0b53e90e707f00d85be1e9afdfe508f9beb51fbe9290629fc501e163be3a585f` |
| `depth_charts` | `depth_charts_2016.parquet` | `aux/depth_2016.parquet` | 472374 | `1f217ac21d2abe7a5c354c4dbd0172a938e281771f10f28ba486e3546008c98f` |
| `depth_charts` | `depth_charts_2017.parquet` | `aux/depth_2017.parquet` | 495128 | `98eec3feb6d25a70fb0aecdfac04d99b27c91cbdc2b51e835f4fe7ce7a1eb315` |
| `depth_charts` | `depth_charts_2018.parquet` | `aux/depth_2018.parquet` | 494788 | `90654261a7c0c1724b7e7a5219229c47cfadd1c15f2d28814daf5fc8a8c534ee` |
| `depth_charts` | `depth_charts_2019.parquet` | `aux/depth_2019.parquet` | 479015 | `797c57613c428d175777a55887f4099a73fc23bb3f46bb01d2d5d46c93bc8d99` |
| `depth_charts` | `depth_charts_2020.parquet` | `aux/depth_2020.parquet` | 462550 | `06c2f2ea95d742aef297188873e3e75043ff4bf67780660251ff09a93980b2c9` |
| `depth_charts` | `depth_charts_2021.parquet` | `aux/depth_2021.parquet` | 485823 | `5afd087b8f3f765d5c88128edcb83d38f7d5137edde58ac5a1ec28c78b79e0df` |
| `depth_charts` | `depth_charts_2022.parquet` | `aux/depth_2022.parquet` | 493072 | `772683d8e2b42ae152008e7bc81acc4cd26a8ed57d1225ebfee4db54cce7b27e` |
| `depth_charts` | `depth_charts_2023.parquet` | `aux/depth_2023.parquet` | 485941 | `44dbf02554d4a8a783f52d53b3c66ede8cc9886097518c56c7e1069f5e2d18ff` |
| `depth_charts` | `depth_charts_2024.parquet` | `aux/depth_2024.parquet` | 482923 | `2b5e72fa37f6a498238da30d619cd5d2fe883ffbfdbaaf637301893ad6c22097` |
| `depth_charts` | `depth_charts_2025.parquet` | `aux/depth_2025.parquet` | 2584724 | `14f74185b3c2c48df234ced284ba3596bda3333d0b2a8df40841f68cd34539fa` |
| `injuries` | `injuries_2014.parquet` | `aux/injuries_2014.parquet` | 115033 | `720a8d4b371e1d0266dd0597dfda9a755038a233998d4f84ec286110664566ef` |
| `injuries` | `injuries_2015.parquet` | `aux/injuries_2015.parquet` | 117594 | `d958742ff453f057b078813cbf6e01c69f9729365937b8b53b8fc262b9e973ba` |
| `injuries` | `injuries_2016.parquet` | `aux/injuries_2016.parquet` | 112500 | `0f771af175147ec1f782aafdc0561e2601f7d843494c533a43d2951115b8495c` |
| `injuries` | `injuries_2017.parquet` | `aux/injuries_2017.parquet` | 114559 | `1838af2b2ff29eb5a0cf8da54a672ebc5835a0f430a0067528d675ea5f5c0a40` |
| `injuries` | `injuries_2018.parquet` | `aux/injuries_2018.parquet` | 113740 | `2ab4c15d0ce8e3afc57fb147d978076cfef848eec0a02f8da14c39b4a4a6db9b` |
| `injuries` | `injuries_2019.parquet` | `aux/injuries_2019.parquet` | 119388 | `b03af1ed05bd6327bb01ce9f12d91ce7eb01ddf605c5755f0643fcfe8702efb4` |
| `injuries` | `injuries_2020.parquet` | `aux/injuries_2020.parquet` | 125134 | `fc8d9c2af770a1af11182a8a3203e8ead780bfb1c08874694f2a7a07b2a1f454` |
| `injuries` | `injuries_2021.parquet` | `aux/injuries_2021.parquet` | 126242 | `14729f0b5debda57df4fce30465a1dd7a34509472669ea01900dbe611b5f9cd7` |
| `injuries` | `injuries_2022.parquet` | `aux/injuries_2022.parquet` | 129611 | `e25a1bd89d5a0c8ac384beef9a08b18de73e4b2e9457e43b4cad7c696ae469a7` |
| `injuries` | `injuries_2023.parquet` | `aux/injuries_2023.parquet` | 126566 | `5d8d881aabd613c3172231b33ac0b4397b3f383498f5e4509c31f3c77c3402a3` |
| `injuries` | `injuries_2024.parquet` | `aux/injuries_2024.parquet` | 139253 | `7ebabbba930a70bc7e4d257c7c41ca8708f147a4cdd4100956abcfbaf70f7fc1` |
| `injuries` | `injuries_2025.parquet` | `aux/injuries_2025.parquet` | 97472 | `c7637c2f6347194426a8582a984aac82a6bf8ab06af72cdd96666a2e0733ab24` |
| `pbp` | `play_by_play_2014.parquet` | `pbp/play_by_play_2014.parquet` | 19360792 | `54c01408d8f0ff9e013a4ea51428b91d610101f72545d082e932f5710e2fb4f7` |
| `pbp` | `play_by_play_2015.parquet` | `pbp/play_by_play_2015.parquet` | 19545842 | `01b5ae7d06633a2e66b418404461a3d4ff055ad2f3a87d453cebcc8d3fda7ed9` |
| `pbp` | `play_by_play_2016.parquet` | `pbp/play_by_play_2016.parquet` | 19344382 | `95eba04e2145e3c1c8ca502f2a3a76cfb0a5990680c3fb480f02a74a45f54a3b` |
| `pbp` | `play_by_play_2017.parquet` | `pbp/play_by_play_2017.parquet` | 19050401 | `84eacd963c1fdd45965f6222c62e9329a7f3412f029d92c1ac5e34a1bb4d3710` |
| `pbp` | `play_by_play_2018.parquet` | `pbp/play_by_play_2018.parquet` | 19072097 | `2e6f2dce7c7ebd46e985cabe0c17eb72b39a77f98cb4478409294f50b5820150` |
| `pbp` | `play_by_play_2019.parquet` | `pbp/play_by_play_2019.parquet` | 19119729 | `60c3067017db2d28a78f66a79b657268be8578d9a5288e6a827efdcd7fe42540` |
| `pbp` | `play_by_play_2020.parquet` | `pbp/play_by_play_2020.parquet` | 19286459 | `8889f5d8782b5c5dce3c3644acdc0322b228a7e9cb13fa36cbac250e260e9f60` |
| `pbp` | `play_by_play_2021.parquet` | `pbp/play_by_play_2021.parquet` | 20249925 | `333ad34378e5339d5172717cc83378e908daf02c8699416ab3e17c2ec10f78d8` |
| `pbp` | `play_by_play_2022.parquet` | `pbp/play_by_play_2022.parquet` | 20426548 | `931121d8897779d7944e2a293e92ed8799c8e5cceef84096ac42339003fedc09` |
| `pbp` | `play_by_play_2023.parquet` | `pbp/play_by_play_2023.parquet` | 20534088 | `bd3484731408def6b0ec93225bba2bd7b2c65769ca707a2b9444d891abdc6776` |
| `pbp` | `play_by_play_2024.parquet` | `pbp/play_by_play_2024.parquet` | 20597560 | `3fd2896bc0b911b615142d2f1fabae54a4bbba5ab7b73b28187b118ef8af6a3b` |
| `pbp` | `play_by_play_2025.parquet` | `pbp/play_by_play_2025.parquet` | 20337029 | `c6ecedd6d678cc37ed316b23ef84ee1ec6abb69c514bb11868a7ebd5a367df29` |
| `players` | `players.parquet` | `aux/players.parquet` | 3394836 | `b4a8a508cd621bfdb4b2e364e1bcee4ab5a945e74fb6a4cfc98af019a50c2812` |
| `rosters` | `roster_2014.parquet` | `aux/roster_2014.parquet` | 350575 | `24ff5af09bf06e7da21bd86213cbff6546cc87bbf679b2516c2e85202621fc05` |
| `rosters` | `roster_2015.parquet` | `aux/roster_2015.parquet` | 370744 | `4c745ba50ba3def3354d924ea871c474e76517a341a6fdc9a5e3d9f212e847eb` |
| `rosters` | `roster_2016.parquet` | `aux/roster_2016.parquet` | 502416 | `a99e54a253f9cfa2ff59158264ddcbe72d05aee601258eaa2ec928bd2400b0f5` |
| `rosters` | `roster_2017.parquet` | `aux/roster_2017.parquet` | 539689 | `e8e6b9b056e4e6e99f72498070dfe7ca217d8c7cb5af9f606c9a0324e8bc3cb5` |
| `rosters` | `roster_2018.parquet` | `aux/roster_2018.parquet` | 573712 | `259532b0bd2f68e01db949023e9f001878c9da9d2c912fef5352324d08b9df07` |
| `rosters` | `roster_2019.parquet` | `aux/roster_2019.parquet` | 582005 | `8aeee315dbf27fc07d5a41a65ac0be0cb00bd63c4a4a23298e0b15228cedd49f` |
| `rosters` | `roster_2020.parquet` | `aux/roster_2020.parquet` | 582955 | `f511f31fe0472c0d0bca87583240c68ca7dc1294f9b7975f5793655bd4598948` |
| `rosters` | `roster_2021.parquet` | `aux/roster_2021.parquet` | 559243 | `6201527093636f44ac74d94f88d57b65bc48c3818c65854ee092b742d6ae6e47` |
| `rosters` | `roster_2022.parquet` | `aux/roster_2022.parquet` | 559611 | `1d8e7987d2b07f510fdce9977354a960041064fdc4a51816cb51540f7835e193` |
| `rosters` | `roster_2023.parquet` | `aux/roster_2023.parquet` | 554634 | `66dcb7d0e203c41b49e997b93eb55ed77bcc960e5dd0774b7e2426a87cef3c90` |
| `rosters` | `roster_2024.parquet` | `aux/roster_2024.parquet` | 566179 | `bd38dbe785728c6b4ddc0b56b7d4fee69a8337595db3b631528d284253c04aaf` |
| `rosters` | `roster_2025.parquet` | `aux/roster_2025.parquet` | 585103 | `902904a0500f066f02ef23f7857e3db35060517e50ffcfe36cc1bc5c6d230f70` |
| `schedules` | `games.csv` | `games.csv` | 2186537 | `3e0a363bd2ef79d53c271965c06e9c3b8c6e3fff7f697be565cbc8e53981df0d` |
| `snap_counts` | `snap_counts_2014.parquet` | `aux/snaps_2014.parquet` | 211616 | `cf7c12f3813a701b43ef9f5dc9d175a6fc1b0113584901b0ccbf71fbccc9ba03` |
| `snap_counts` | `snap_counts_2015.parquet` | `aux/snaps_2015.parquet` | 211957 | `65fcdc9cf79f641e0851988635395421895523288cccdd00361bec06875272cb` |
| `snap_counts` | `snap_counts_2016.parquet` | `aux/snaps_2016.parquet` | 212500 | `02fbeb10dd1369d858ac460ef67b0a2e9a4da689e24149d81dec57863f975198` |
| `snap_counts` | `snap_counts_2017.parquet` | `aux/snaps_2017.parquet` | 212468 | `46805183af6d987bac7292a84d787343bd10d5323fba96e9ce624cfe9c085dc5` |
| `snap_counts` | `snap_counts_2018.parquet` | `aux/snaps_2018.parquet` | 211973 | `db5ab19bb95e290ce38cd73c40795b15ecf9b01cbc90b6f339f8010c51c1d506` |
| `snap_counts` | `snap_counts_2019.parquet` | `aux/snaps_2019.parquet` | 213089 | `ef9068cc9eebaf01fa78c783a29cc1658a32b35fd7226e98146aa178e13aedc2` |
| `snap_counts` | `snap_counts_2020.parquet` | `aux/snaps_2020.parquet` | 233598 | `0dee77ab7595cbef05ce6239f04e7a97c56955e5a9b376dcccf58324f4dd8abc` |
| `snap_counts` | `snap_counts_2021.parquet` | `aux/snaps_2021.parquet` | 240680 | `1f6ab4c26a7d394c857652234dd39acd4088ffd92cfcf3f91ce03f4dc9fcaa2a` |
| `snap_counts` | `snap_counts_2022.parquet` | `aux/snaps_2022.parquet` | 237678 | `3cfde83eb6d859e3477aba4238fa2fb8e0592a5fc5ab8b26128fce085d4b7fea` |
| `snap_counts` | `snap_counts_2023.parquet` | `aux/snaps_2023.parquet` | 237882 | `97873cab365dfb39286bc1de3ac772fa4b51f09cce52ae5f8317e006cc007f62` |
| `snap_counts` | `snap_counts_2024.parquet` | `aux/snaps_2024.parquet` | 240862 | `9ec66a0c755939b329d4eba3f90e716bb874b5a6544f325ba220f003991ff99f` |
| `snap_counts` | `snap_counts_2025.parquet` | `aux/snaps_2025.parquet` | 242046 | `47220218fecda7af7ee5b0272a8ecce7c1bfb1f4e1f0a2c8c90047571728ce20` |

File count: 62. Download errors: 0.
