use std::{borrow::Cow, marker::PhantomData};

use libafl::{
    corpus::Corpus,
    inputs::{BytesInput, HasMutatorBytes, HasTargetBytes, UsesInput},
    mutators::{self, havoc_mutations, I2SRandReplace, MutationResult, Mutator},
    stages::Stage,
    state::{HasCorpus, HasCurrentTestcase, HasRand, State, UsesState},
    Evaluator, HasMetadata,
};
use libafl_bolts::{
    impl_serdeany,
    tuples::{tuple_list, tuple_list_type, Map, MappingFunctor, Merge},
    Error, Named,
};
use serde::{Deserialize, Serialize};

use crate::formatfuzzer_wrapper::{DecisionSeed, FileHandle, InputData};

use super::formatfuzzer_wrapper::FormatFuzzer;

#[derive(Debug, Default, Serialize, Deserialize, Clone)]
pub struct FormatFuzzerMetadata {
    #[serde(skip)]
    handle: Option<FileHandle>,
    validity: f32,
    decisionseed: Vec<u8>,
}
impl_serdeany!(FormatFuzzerMetadata);

#[derive(Debug, Default, Serialize, Deserialize)]
pub struct FormatFuzzerStateMetadata {
    #[serde(skip)]
    handle: Option<FormatFuzzer<'static>>,
}
impl_serdeany!(FormatFuzzerStateMetadata);

pub struct DecisionSeedWrapper<T>(T);

impl<T: Named> DecisionSeedWrapper<T> {
    fn to_seed<S>(input: &[u8], state: &mut S) -> Vec<u8>
    where
        S: State + HasMetadata,
    {
        let ff = state.metadata::<FormatFuzzerStateMetadata>().unwrap();
        match ff.handle.as_ref().unwrap().parse(InputData(input.into())) {
            Ok(x) => x.0.to_vec(),
            Err(x) => x.0.to_vec(),
        }
    }
    fn to_input<S>(seed: &[u8], state: &mut S) -> Vec<u8>
    where
        S: State + HasMetadata,
    {
        let ff = state.metadata::<FormatFuzzerStateMetadata>().unwrap();
        ff.handle
            .as_ref()
            .unwrap()
            .generate(DecisionSeed(seed.into()))
            .0
            .to_vec()
    }
}

impl<T: Named, S> Mutator<BytesInput, S> for DecisionSeedWrapper<T>
where
    for<'a> T: Mutator<BytesInput, S>,
    S: State + HasMetadata,
{
    fn mutate(&mut self, state: &mut S, input: &mut BytesInput) -> Result<MutationResult, Error> {
        // let mapped = &mut (self.mapper)(input);
        let mut decision_seed = BytesInput::new(Self::to_seed(input.bytes(), state));
        match self.0.mutate(state, &mut decision_seed) {
            Ok(MutationResult::Mutated) => {
                input.drain(..);
                input.extend(&Self::to_input(decision_seed.bytes(), state));
                Ok(MutationResult::Mutated)
            }
            res => res,
        }
    }
}

impl<T: Named> Named for DecisionSeedWrapper<T> {
    fn name(&self) -> &Cow<'static, str> {
        &self.0.name()
    }
}

struct DecisionSeedMapper;
impl<T> MappingFunctor<T> for DecisionSeedMapper {
    type Output = DecisionSeedWrapper<T>;

    fn apply(&mut self, from: T) -> Self::Output {
        DecisionSeedWrapper(from)
    }
}

type DecisionSeedHavocMutations = tuple_list_type!(
    DecisionSeedWrapper<I2SRandReplace>,
    DecisionSeedWrapper<mutators::BitFlipMutator>,
    DecisionSeedWrapper<mutators::ByteFlipMutator>,
    DecisionSeedWrapper<mutators::ByteIncMutator>,
    DecisionSeedWrapper<mutators::ByteDecMutator>,
    DecisionSeedWrapper<mutators::ByteNegMutator>,
    DecisionSeedWrapper<mutators::ByteRandMutator>,
    DecisionSeedWrapper<mutators::ByteAddMutator>,
    DecisionSeedWrapper<mutators::WordAddMutator>,
    DecisionSeedWrapper<mutators::DwordAddMutator>,
    DecisionSeedWrapper<mutators::QwordAddMutator>,
    DecisionSeedWrapper<mutators::ByteInterestingMutator>,
    DecisionSeedWrapper<mutators::WordInterestingMutator>,
    DecisionSeedWrapper<mutators::DwordInterestingMutator>,
    DecisionSeedWrapper<mutators::BytesDeleteMutator>,
    DecisionSeedWrapper<mutators::BytesDeleteMutator>,
    DecisionSeedWrapper<mutators::BytesDeleteMutator>,
    DecisionSeedWrapper<mutators::BytesDeleteMutator>,
    DecisionSeedWrapper<mutators::BytesExpandMutator>,
    DecisionSeedWrapper<mutators::BytesInsertMutator>,
    DecisionSeedWrapper<mutators::BytesRandInsertMutator>,
    DecisionSeedWrapper<mutators::BytesSetMutator>,
    DecisionSeedWrapper<mutators::BytesRandSetMutator>,
    DecisionSeedWrapper<mutators::BytesCopyMutator>,
    DecisionSeedWrapper<mutators::BytesInsertCopyMutator>,
    DecisionSeedWrapper<mutators::BytesSwapMutator>,
    DecisionSeedWrapper<mutators::CrossoverInsertMutator>,
    DecisionSeedWrapper<mutators::CrossoverReplaceMutator>,
);

pub fn decision_seed_mutations() -> DecisionSeedHavocMutations {
    tuple_list!(I2SRandReplace).map(DecisionSeedMapper).merge(
        // tuple_list_type!(DecisionSeedWrapper<I2SRandReplace>) {
        havoc_mutations().map(DecisionSeedMapper),
    )
    // tuple_list!(DecisionSeedWrapper(I2SRandReplace))
}

#[derive(Default, Debug)]
pub struct OneSmartMutator;

impl<I, S> Mutator<I, S> for OneSmartMutator
where
    S: HasRand + HasMetadata,
    I: HasMutatorBytes,
{
    fn mutate(&mut self, state: &mut S, input: &mut I) -> Result<MutationResult, Error> {
        if !state.has_metadata::<FormatFuzzerMetadata>() {
            dbg!("No metadata found?");
            return Ok(MutationResult::Skipped);
        }

        let fh = state
            .metadata::<FormatFuzzerMetadata>()
            .unwrap()
            .handle
            .as_ref()
            .unwrap();

        let ff = state.metadata::<FormatFuzzerStateMetadata>().unwrap();
        let data = ff.handle.as_ref().unwrap().one_smart_mutation(fh);
        input.drain(..);
        input.extend(data.0.as_ref());
        Ok(MutationResult::Mutated)
    }
}

impl Named for OneSmartMutator {
    fn name(&self) -> &Cow<'static, str> {
        static NAME: Cow<'static, str> = Cow::Borrowed("OneSmartMutator");
        &NAME
    }
}

type DecisionSeedSmartMutations = tuple_list_type!(OneSmartMutator);

pub fn smart_mutations() -> DecisionSeedSmartMutations {
    tuple_list!(OneSmartMutator)
}

#[derive(Debug)]
pub struct FormatFuzzerProcessStage<'a, S> {
    phantom: PhantomData<S>,
    formatfuzzer: FormatFuzzer<'a>,
}

impl<'a, S> FormatFuzzerProcessStage<'a, S> {
    /// Create a new instance of the string identification stage
    #[must_use]
    pub fn new(formatfuzzer: FormatFuzzer<'a>) -> Self {
        Self {
            phantom: PhantomData,
            formatfuzzer,
        }
    }

    fn add_metadata_to_current_testcase(&mut self, state: &mut S) -> Result<FileHandle, Error>
    where
        S: HasCurrentTestcase,
        <S::Corpus as Corpus>::Input: HasTargetBytes,
    {
        let mut tc = state.current_testcase_mut()?;
        if let Ok(md) = tc.metadata::<FormatFuzzerMetadata>() {
            if let Some(handle) = md.handle.as_ref() {
                return Ok(handle.clone()); // skip recompute
            }
        }

        let input = tc.load_input(state.corpus())?;

        let bytes = input.target_bytes();
        let handle = self.formatfuzzer.process_file(&bytes);
        let decisionseed = self.formatfuzzer.parse(InputData((&*bytes).into()));
        let decisionseed = match decisionseed {
            Ok(x) => x,
            Err(x) => x,
        };
        let decisionseed = decisionseed.0.to_vec();
        tc.add_metadata(FormatFuzzerMetadata {
            decisionseed,
            validity: handle.validity,
            handle: Some(handle.clone()),
        });

        Ok(handle)
    }
}

impl<'a, S> UsesState for FormatFuzzerProcessStage<'a, S>
where
    S: State,
{
    type State = S;
}

impl<'a, S, E, EM, Z> Stage<E, EM, Z> for FormatFuzzerProcessStage<'a, S>
where
    S: HasCorpus + State + HasCurrentTestcase + UsesInput<Input = BytesInput>,
    S::Corpus: Corpus<Input = BytesInput>,
    E: UsesState<State = S>,
    EM: UsesState<State = S>,
    Z: UsesState<State = S> + Evaluator<E, EM, State = S>,
{
    fn perform(
        &mut self,
        fuzzer: &mut Z,
        executor: &mut E,
        state: &mut Self::State,
        manager: &mut EM,
    ) -> Result<(), Error> {
        let handle = self.add_metadata_to_current_testcase(state)?;

        // TODO: run smart mutations
        for _ in 0..10 {
            let input = self.formatfuzzer.one_smart_mutation(&handle);
            // let input = self.formatfuzzer.generate(seed);
            let input = BytesInput::new(input.0.to_vec());
            let (_res, _corpus_id) = fuzzer.evaluate_input(state, executor, manager, input)?;

            // self.mutator_mut().post_exec(state, corpus_id)?;
            // post.post_exec(state, corpus_id)?;
        }

        // TODO: run non-smart mutations? i.e. i2s replace on decision seed

        Ok(())
    }

    #[inline]
    fn should_restart(&mut self, _state: &mut Self::State) -> Result<bool, Error> {
        // Stage does not run the target. No reset helper needed.
        Ok(true)
    }

    #[inline]
    fn clear_progress(&mut self, _state: &mut Self::State) -> Result<(), Error> {
        // Stage does not run the target. No reset helper needed.
        Ok(())
    }
}
