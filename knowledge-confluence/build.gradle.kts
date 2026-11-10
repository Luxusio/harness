plugins {
    kotlin("jvm") version "2.0.21"
    kotlin("plugin.serialization") version "2.0.21"
    id("com.github.johnrengelman.shadow") version "8.1.1"
    application
}
repositories { mavenCentral() }
val ktorVersion = "2.3.12"
dependencies {
    implementation("io.ktor:ktor-client-core:$ktorVersion")
    implementation("io.ktor:ktor-client-cio:$ktorVersion")
    implementation("io.ktor:ktor-client-content-negotiation:$ktorVersion")
    implementation("io.ktor:ktor-serialization-kotlinx-json:$ktorVersion")
    implementation("io.ktor:ktor-client-auth:$ktorVersion")
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.7.3")
    implementation("org.jsoup:jsoup:1.18.1")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-core:1.9.0")
    implementation("org.slf4j:slf4j-simple:2.0.16")
    implementation("org.yaml:snakeyaml:1.33")
    implementation(kotlin("reflect"))
    testImplementation(kotlin("test"))
    testImplementation("org.junit.jupiter:junit-jupiter:5.11.4")
    testImplementation("io.ktor:ktor-client-mock:$ktorVersion")
}
kotlin { jvmToolchain(21) }
application { mainClass.set("io.crscube.clitools.knowledge.MainKt") }
tasks.test { useJUnitPlatform() }
tasks.jar { archiveClassifier.set("plain") }
tasks.shadowJar {
    archiveBaseName.set("knowledge-confluence")
    archiveClassifier.set("")
    archiveVersion.set("")
    manifest { attributes("Main-Class" to "io.crscube.clitools.knowledge.MainKt") }
    mergeServiceFiles()
}
tasks.build { dependsOn(tasks.shadowJar) }
